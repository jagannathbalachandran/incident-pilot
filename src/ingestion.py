"""
Ingestion pipeline — Week 1, Task 6.

Loads runbooks and postmortems, strips YAML frontmatter, splits on ## headers,
embeds with HuggingFace all-MiniLM-L6-v2, and writes a persistent ChromaDB
vector store. Deletes and recreates the store on every run so it always
reflects the current state of the corpus.

Usage:
    python src/ingestion.py
"""

import logging
import re
import shutil
from pathlib import Path

from langchain_text_splitters import MarkdownHeaderTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

from logging_config import setup_logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
RUNBOOKS_DIR = REPO_ROOT / "synthetic-data" / "runbooks"
POSTMORTEMS_DIR = REPO_ROOT / "synthetic-data" / "postmorterms"
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
            logger.debug("Loaded %s (%d chars)", path.name, len(text))
    return docs


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def build_vectorstore() -> Chroma:
    logger.info("=== Ingestion Pipeline ===")

    # 1. Wipe and recreate the vector store directory
    if VECTORSTORE_DIR.exists():
        shutil.rmtree(VECTORSTORE_DIR)
        logger.info("Deleted existing vector store at %s", VECTORSTORE_DIR)
    VECTORSTORE_DIR.mkdir(parents=True)
    logger.info("Created fresh vector store directory")

    # 2. Load and chunk documents
    logger.info("Loading and chunking documents...")
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
        logger.info("  %s: %d chunks", source, len(chunks))

    logger.info("Total chunks: %d", len(all_chunks))

    # 3. Embed
    logger.info("Loading embedding model (all-MiniLM-L6-v2)...")
    embeddings = HuggingFaceEmbeddings(
        model_name="all-MiniLM-L6-v2",
        model_kwargs={"device": "cpu"},
    )

    # 4. Build and persist ChromaDB
    logger.info("Building ChromaDB vector store...")
    vectorstore = Chroma.from_documents(
        documents=all_chunks,
        embedding=embeddings,
        persist_directory=str(VECTORSTORE_DIR),
    )
    logger.info("Vector store saved to %s (%d chunks indexed)",
                 VECTORSTORE_DIR, len(all_chunks))
    return vectorstore


def query_vectorstore(vectorstore: Chroma, query: str, k: int = 3) -> None:
    logger.info("Querying vector store: '%s' (k=%d)", query, k)
    results = vectorstore.similarity_search(query, k=k)
    for i, doc in enumerate(results, 1):
        source = doc.metadata.get("source", "unknown")
        section = doc.metadata.get("section", "unknown")
        logger.info("Result %d | %s | %s", i, source, section)
        logger.debug("Content: %s...", doc.page_content[:200])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    setup_logging()
    vectorstore = build_vectorstore()
    query_vectorstore(vectorstore, "connection pool exhaustion")
    logger.info("Ingestion complete")
