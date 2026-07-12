"""
Ingestion pipeline — Week 1, Task 6.

Loads runbooks and postmortems, strips YAML frontmatter, splits on ## headers,
embeds with HuggingFace all-MiniLM-L6-v2, and writes a persistent ChromaDB
vector store. Deletes and recreates the store on every run so it always
reflects the current state of the corpus.

Usage:
    python src/ingestion.py
"""

import re
import shutil
from pathlib import Path

from langchain_text_splitters import MarkdownHeaderTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
RUNBOOKS_DIR = REPO_ROOT / "runbooks"
POSTMORTEMS_DIR = REPO_ROOT / "postmorterms"
VECTORSTORE_DIR = REPO_ROOT / "synthetic-data" / "vectorstore"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter block from the top of a markdown document."""
    return FRONTMATTER_RE.sub("", text).strip()


def load_documents(directories: list[Path]) -> list[tuple[str, str]]:
    """Return (source_filename, content) for every .md file in the given dirs."""
    docs = []
    for directory in directories:
        for path in sorted(directory.glob("*.md")):
            text = path.read_text()
            docs.append((path.name, strip_frontmatter(text)))
    return docs


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def build_vectorstore() -> Chroma:
    # 1. Wipe and recreate the vector store directory
    if VECTORSTORE_DIR.exists():
        shutil.rmtree(VECTORSTORE_DIR)
        print(f"Deleted existing vector store at {VECTORSTORE_DIR}")
    VECTORSTORE_DIR.mkdir(parents=True)

    # 2. Load and chunk documents
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("##", "section")],
        strip_headers=False,
    )

    all_chunks = []
    for source, content in load_documents([RUNBOOKS_DIR, POSTMORTEMS_DIR]):
        chunks = splitter.split_text(content)
        for chunk in chunks:
            chunk.metadata["source"] = source
        all_chunks.extend(chunks)
        print(f"  {source}: {len(chunks)} chunks")

    print(f"\nTotal chunks: {len(all_chunks)}")

    # 3. Embed
    print("\nLoading embedding model (all-MiniLM-L6-v2)...")
    embeddings = HuggingFaceEmbeddings(
        model_name="all-MiniLM-L6-v2",
        model_kwargs={"device": "cpu"},
    )

    # 4. Build and persist ChromaDB
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
        print(f"\n--- Result {i} | {source} | {section} ---")
        print(doc.page_content[:600])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== IncidentPilot Ingestion Pipeline ===\n")
    print("Chunking documents:")
    vectorstore = build_vectorstore()
    query_vectorstore(vectorstore, "connection pool exhaustion")
