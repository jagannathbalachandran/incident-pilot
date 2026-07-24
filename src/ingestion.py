"""
Ingestion pipeline.

Real-runbook corpus (synthetic-data/real-runbooks/) and postmortems
(synthetic-data/postmorterms/):
  Both are treated as heterogeneous enterprise documents of unknown format
  (today postmortems happen to be markdown, and real-runbooks are a mix of
  PDF and DOCX — the pipeline doesn't assume any single format; new formats
  in either directory just need an extractor added to
  REAL_RUNBOOK_EXTRACTORS). Both are chunked with SemanticChunker, which
  embeds sentences and splits where meaning shifts significantly. Chunking
  itself is format-agnostic — only text extraction is format-specific.

  Safety net: any chunk exceeding MAX_CHUNK_CHARS is further split by
  RecursiveCharacterTextSplitter to stay within the embedding model's
  token limit (all-MiniLM-L6-v2 max: 256 tokens ≈ 1000 chars).

  The embedding model is created once and shared between SemanticChunker
  and ChromaDB to avoid loading it twice.

synthetic-data/runbooks/ is not currently indexed.

Usage:
    python src/ingestion.py
"""

import re
import shutil
from pathlib import Path

from langchain_core.documents import Document
from langchain_experimental.text_splitter import SemanticChunker
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
POSTMORTEMS_DIR    = REPO_ROOT / "synthetic-data" / "postmorterms"
REAL_RUNBOOKS_DIR  = REPO_ROOT / "synthetic-data" / "real-runbooks"
VECTORSTORE_DIR    = REPO_ROOT / "synthetic-data" / "vectorstore"

# Chunks exceeding this length get a secondary split so they stay within the
# embedding model's token limit.
MAX_CHUNK_CHARS = 1500

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
    Secondary pass: split any chunk that exceeds MAX_CHUNK_CHARS.
    Splits at paragraph → sentence → word boundaries, never mid-sentence.
    Preserves all metadata from the parent chunk.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=MAX_CHUNK_CHARS,
        chunk_overlap=100,
        separators=["\n\n", "\n", ". ", " "],
    )
    result = []
    for doc in chunks:
        if len(doc.page_content) <= MAX_CHUNK_CHARS:
            result.append(doc)
        else:
            result.extend(splitter.split_documents([doc]))
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

def build_vectorstore() -> Chroma:
    # 1. Wipe and recreate
    if VECTORSTORE_DIR.exists():
        shutil.rmtree(VECTORSTORE_DIR)
        print(f"Deleted existing vector store at {VECTORSTORE_DIR}")
    VECTORSTORE_DIR.mkdir(parents=True)

    # 2. Create embedding model once — shared by SemanticChunker and ChromaDB
    print("\nLoading embedding model (all-MiniLM-L6-v2)...")
    embeddings = HuggingFaceEmbeddings(
        model_name="all-MiniLM-L6-v2",
        model_kwargs={"device": "cpu"},
    )

    all_chunks: list[Document] = []

    # 3. Real-runbook corpus (PDF, DOCX, ...) — semantic chunking, format-agnostic
    print("\nReal-runbook corpus (semantic chunking):")
    if REAL_RUNBOOKS_DIR.exists():
        for path in sorted(REAL_RUNBOOKS_DIR.iterdir()):
            if not path.is_file():
                continue
            try:
                text = extract_real_runbook_text(path)
            except ValueError as e:
                print(f"  {path.name}: SKIPPED — {e}")
                continue
            chunks = semantic_chunk_text(text, path.name, embeddings)
            all_chunks.extend(chunks)
            print(f"  {path.name}: {len(chunks)} chunks [semantic]")

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
