"""
Ingestion pipeline.

Indexes two corpora: runbooks (synthetic-data/runbooks/) and postmortems
(synthetic-data/postmorterms/) -- both markdown, frontmatter stripped, then
chunked with SemanticChunker, which embeds sentences and splits where
meaning shifts significantly (not a plain ## header split).

  Safety net: any chunk exceeding MAX_CHUNK_TOKENS (measured with the same
  tokenizer the embedding model uses) is further split by
  RecursiveCharacterTextSplitter to stay within the embedding model's
  token limit (all-MiniLM-L6-v2 max: 256 tokens).

  The embedding model is created once and shared between SemanticChunker
  and ChromaDB to avoid loading it twice.

synthetic-data/real-runbooks/ (PDF/DOCX) is not currently indexed -- the
format-agnostic extractors below (extract_pdf_text/extract_docx_text) are
kept in case that source is switched back on.

Usage:
    python src/ingestion.py
"""

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.documents import Document
from langchain_experimental.text_splitter import SemanticChunker
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from transformers import AutoTokenizer

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
POSTMORTEMS_DIR    = REPO_ROOT / "synthetic-data" / "postmorterms"
RUNBOOKS_DIR       = REPO_ROOT / "synthetic-data" / "runbooks"
REAL_RUNBOOKS_DIR  = REPO_ROOT / "synthetic-data" / "real-runbooks"
VECTORSTORE_DIR    = REPO_ROOT / "synthetic-data" / "vectorstore"

# Single source of truth for the embedding model -- used for the tokenizer
# below, the HuggingFaceEmbeddings instance in build_vectorstore(), and the
# ingestion metadata stamp, so there's exactly one place to change for a
# model swap instead of several independently hardcoded strings.
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

# Must match the embedding model used in build_vectorstore() below -- this
# tokenizer is what determines whether a chunk will get truncated at embed
# time, so it's also what has to decide whether a chunk needs splitting.
EMBEDDING_TOKENIZER = AutoTokenizer.from_pretrained(f"sentence-transformers/{EMBEDDING_MODEL_NAME}")

# all-MiniLM-L6-v2's hard limit is 256 tokens (sentence_bert_config.json).
# Chunks exceeding this get a secondary split so they stay within it.
# Set below 256 to leave headroom for [CLS]/[SEP] and tokenizer edge cases.
MAX_CHUNK_TOKENS = 230

# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter block from the top of a markdown document."""
    return FRONTMATTER_RE.sub("", text).strip()


def load_markdown_documents(directories: list[Path]) -> list[tuple[str, str]]:
    """Return (filename, content) for every .md file in the given directories."""
    docs = []
    for directory in directories:
        for path in sorted(directory.glob("*.md")):
            text = path.read_text()
            docs.append((path.name, strip_frontmatter(text)))
    return docs


# ---------------------------------------------------------------------------
# Real-runbook text extraction — dispatches on file extension
# ---------------------------------------------------------------------------

def extract_pdf_text(path: Path) -> str:
    """Extract plain text from a PDF, page by page, joined with blank lines."""
    import pdfplumber
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text and text.strip():
                pages.append(text.strip())
    return "\n\n".join(pages)


def extract_docx_text(path: Path) -> str:
    """Extract plain text from a Word document, in document order (paragraphs
    and tables interleaved as they appear), joined with blank lines."""
    import docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(path)
    parts = []
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            text = Paragraph(child, document).text.strip()
            if text:
                parts.append(text)
        elif child.tag == qn("w:tbl"):
            for row in Table(child, document).rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    parts.append("\t".join(cells))
    return "\n\n".join(parts)


# Extension -> extractor. Add new formats here as they show up in the corpus.
REAL_RUNBOOK_EXTRACTORS = {
    ".pdf": extract_pdf_text,
    ".docx": extract_docx_text,
    ".txt": lambda path: path.read_text(),
}


def extract_real_runbook_text(path: Path) -> str:
    """Extract plain text from a real-runbook document, dispatching on file
    extension. Raises on unrecognized formats rather than silently skipping
    them — a document is worth flagging even if we don't know how to read it."""
    extractor = REAL_RUNBOOK_EXTRACTORS.get(path.suffix.lower())
    if extractor is None:
        raise ValueError(f"unsupported file format {path.suffix!r}")
    return extractor(path)


# ---------------------------------------------------------------------------
# Semantic chunking (format-agnostic — used for PDFs and postmortems alike)
# ---------------------------------------------------------------------------

def _safety_split(chunks: list[Document]) -> list[Document]:
    """
    Secondary pass: split any chunk that exceeds MAX_CHUNK_TOKENS, measured
    with the embedding model's own tokenizer -- not a character-count proxy,
    which under- or overestimates badly on paths/identifiers/commands (e.g.
    "infra/pgbouncer/payment-pool.ini") that tokenize less efficiently than
    prose. Splits at paragraph → sentence → word boundaries, never
    mid-sentence. Preserves all metadata from the parent chunk.

    Each resulting piece is tagged with metadata["parent_content"] holding
    the full, undivided text it was split from. The split pieces still get
    embedded and searched individually (so each stays within the embedding
    model's token limit), but retrieval can substitute parent_content back
    in instead of the piece's own (possibly incomplete) content -- so a
    chunk that had to be split for length still returns its full original
    section, not a fragment missing its header or cut mid-instruction.
    """
    splitter = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
        EMBEDDING_TOKENIZER,
        chunk_size=MAX_CHUNK_TOKENS,
        chunk_overlap=30,
        separators=["\n\n", "\n", ". ", " "],
    )
    result = []
    for doc in chunks:
        n_tokens = len(EMBEDDING_TOKENIZER(doc.page_content)["input_ids"])
        if n_tokens <= MAX_CHUNK_TOKENS:
            result.append(doc)
        else:
            pieces = splitter.split_documents([doc])
            for piece in pieces:
                piece.metadata["parent_content"] = doc.page_content
            result.extend(pieces)
    return result


def semantic_chunk_text(
    text: str,
    source: str,
    embeddings: HuggingFaceEmbeddings,
) -> list[Document]:
    """
    Chunk a document's plain text using semantic similarity.

    SemanticChunker embeds every sentence and finds points where the meaning
    shifts significantly (95th percentile of all pairwise distances in the
    document). No knowledge of the document's format is required — the same
    function handles ISTM tables, formal numbered templates, prose-style docs,
    markdown postmortems, and any other format without configuration.

    The embeddings object is passed in (not created here) so the caller can
    reuse the same model instance for both chunking and the vector store.
    """
    chunker = SemanticChunker(
        embeddings,
        breakpoint_threshold_type="percentile",
        breakpoint_threshold_amount=95,
    )
    chunks = chunker.create_documents([text])
    for chunk in chunks:
        # Use the first non-empty line as a human-readable section label.
        first_line = next(
            (line.strip() for line in chunk.page_content.split("\n") if line.strip()),
            "unknown",
        )
        chunk.metadata["source"] = source
        chunk.metadata["section"] = first_line[:80]
        chunk.metadata["format"] = "semantic"

    return _safety_split(chunks)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return "unknown"


def _write_ingestion_metadata(vectorstore: Chroma, chunk_count: int) -> None:
    """Stamp what actually built this vectorstore, written once at ingestion
    time -- not re-derived later from whatever ingestion.py's constants
    currently say, which could have changed since without a re-ingest. This
    is what lets a benchmark run detect "the vectorstore on disk doesn't
    match what the current code would produce" instead of silently assuming
    they match.
    """
    collection_metadata = vectorstore._collection.metadata
    hnsw_space = (collection_metadata or {}).get("hnsw:space", "l2 (chroma implicit default)")

    metadata = {
        "embedding_model": EMBEDDING_MODEL_NAME,
        "max_chunk_tokens": MAX_CHUNK_TOKENS,
        "hnsw_space": hnsw_space,
        "chunk_count": chunk_count,
        "git_commit": _git_commit(),
        "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path = VECTORSTORE_DIR / "_ingestion_metadata.json"
    path.write_text(json.dumps(metadata, indent=2))
    print(f"Ingestion metadata saved to {path}")


def build_vectorstore() -> Chroma:
    # 1. Wipe and recreate
    if VECTORSTORE_DIR.exists():
        shutil.rmtree(VECTORSTORE_DIR)
        print(f"Deleted existing vector store at {VECTORSTORE_DIR}")
    VECTORSTORE_DIR.mkdir(parents=True)

    # 2. Create embedding model once — shared by SemanticChunker and ChromaDB
    print(f"\nLoading embedding model ({EMBEDDING_MODEL_NAME})...")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL_NAME,
        model_kwargs={"device": "cpu"},
    )

    all_chunks: list[Document] = []

    # 3. Runbook corpus (markdown) — semantic chunking, not a ## header split
    print("\nRunbook corpus (semantic chunking):")
    if RUNBOOKS_DIR.exists():
        for filename, content in load_markdown_documents([RUNBOOKS_DIR]):
            chunks = semantic_chunk_text(content, filename, embeddings)
            all_chunks.extend(chunks)
            print(f"  {filename}: {len(chunks)} chunks [semantic]")

    # 4. Postmortems — same semantic chunking, treated as format-agnostic too
    print("\nPostmortem corpus (semantic chunking):")
    if POSTMORTEMS_DIR.exists():
        for filename, content in load_markdown_documents([POSTMORTEMS_DIR]):
            chunks = semantic_chunk_text(content, filename, embeddings)
            all_chunks.extend(chunks)
            print(f"  {filename}: {len(chunks)} chunks [semantic]")

    print(f"\nTotal chunks: {len(all_chunks)}")

    # 5. Build and persist ChromaDB using the same embeddings instance
    print("Building ChromaDB vector store...")
    vectorstore = Chroma.from_documents(
        documents=all_chunks,
        embedding=embeddings,
        persist_directory=str(VECTORSTORE_DIR),
    )
    print(f"Vector store saved to {VECTORSTORE_DIR}")

    _write_ingestion_metadata(vectorstore, chunk_count=len(all_chunks))
    return vectorstore


def query_vectorstore(vectorstore: Chroma, query: str, k: int = 3) -> None:
    print(f"\n{'='*60}")
    print(f"Query: \"{query}\"")
    print(f"{'='*60}")
    results = vectorstore.similarity_search(query, k=k)
    for i, doc in enumerate(results, 1):
        source = doc.metadata.get("source", "unknown")
        section = doc.metadata.get("section", "unknown")
        fmt = doc.metadata.get("format", "unknown")
        print(f"\n--- Result {i} | {source} | {section} | [{fmt}] ---")
        print(doc.page_content)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== IncidentPilot Ingestion Pipeline ===")
    vectorstore = build_vectorstore()
    query_vectorstore(vectorstore, "connection pool exhaustion in checkout service")
    query_vectorstore(vectorstore, "high latency in checkout service")
    query_vectorstore(vectorstore, "error in add to cart service")
