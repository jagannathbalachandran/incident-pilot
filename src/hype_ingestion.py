"""
HyPE (Hypothetical Prompt Embeddings) ingestion.

The inverse of HyDE: instead of expanding the engineer's query into
runbook-vocabulary search queries at *query* time (one live LLM call per
query, non-deterministic even at temperature=0), HyPE generates several
hypothetical questions each chunk would answer at *ingestion* time (one-off
LLM cost, amortized across every future query), embeds those questions
instead of the chunk's own prose, and matches the engineer's raw query
directly against those precomputed question embeddings. This turns
retrieval into a question-vs-question (symmetric) match instead of a
question-vs-passage (asymmetric) one, and removes the runtime LLM call
from retrieval entirely.

Deliberately reads its input chunks from the already-built main vector
store (src/ingestion.py's output) rather than re-chunking the corpus --
this guarantees HyPE and HyDE are compared against byte-identical chunks,
so the only variable between the two eval modes is the retrieval
mechanism, not incidental chunking differences.

Stores into a SEPARATE Chroma collection (synthetic-data/vectorstore_hype/)
so this never touches or overwrites the main vector store.

Usage:
    uv run python src/hype_ingestion.py
"""

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from langchain_chroma import Chroma
from langchain_groq import ChatGroq
from langchain_huggingface import HuggingFaceEmbeddings

import os

REPO_ROOT = Path(__file__).parent.parent
load_dotenv(REPO_ROOT / ".env")

MAIN_VECTORSTORE_DIR = REPO_ROOT / "synthetic-data" / "vectorstore"
HYPE_VECTORSTORE_DIR = REPO_ROOT / "synthetic-data" / "vectorstore_hype"

# Must match src/ingestion.py's EMBEDDING_MODEL_NAME -- HyPE embeds questions
# with the same model HyDE's dense search already uses, so the only variable
# between the two eval modes is the retrieval mechanism, not the embedder.
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

# Same model + temperature=0 as HyDE's _expand_query (src/incident_pilot.py)
# -- same reasoning: reproducible ingestion, and a fair comparison means not
# giving HyPE a different/stronger LLM than HyDE gets.
LLM_MODEL_NAME = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
LLM_TEMPERATURE = 0

QUESTIONS_PER_CHUNK = 3

QUESTION_GENERATION_PROMPT = """\
You are indexing a runbook/postmortem chunk for a semantic search system \
used by on-call engineers during incident triage.

This chunk is from the document "{source}" -- infer which service that \
document is primarily about from its filename and content. Even where the \
chunk mentions or cross-checks a *different* service (e.g. a step telling \
the on-call engineer to also check another service's dashboard), the \
questions you generate must stay anchored to what THIS document is \
fundamentally about. A cross-check step that mentions another service is \
not itself a question about that other service.

Chunk content:
\"\"\"
{chunk_content}
\"\"\"

Generate exactly {n} questions an on-call engineer might type into this \
search system while actively triaging an incident, that this chunk would \
help answer -- phrased the way an engineer would actually ask under time \
pressure (symptom-focused, casual), not the way the chunk itself is \
written. Cover different angles where the chunk supports it (symptom \
description, mitigation/fix step, escalation) -- but every question must \
be genuinely answerable from this specific chunk, not a generic incident \
question, and must not be mistakable for a question about a different \
service just because this chunk happens to mention one.

Return only the {n} questions, one per line. No numbering, no explanation.\
"""


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return "unknown"


def _load_unique_chunks(embeddings: HuggingFaceEmbeddings) -> list[Document]:
    """Unique chunks from the main vector store, deduped by parent_content
    (same substitution rule as _retrieve_with_queries -- a chunk split for
    length has multiple embedded pieces sharing one parent_content; HyPE
    should generate questions per logical chunk, not per split piece)."""
    if not MAIN_VECTORSTORE_DIR.exists():
        raise SystemExit(f"No main vector store at {MAIN_VECTORSTORE_DIR} -- run src/ingestion.py first.")

    vectorstore = Chroma(persist_directory=str(MAIN_VECTORSTORE_DIR), embedding_function=embeddings)
    got = vectorstore.get()

    seen: dict[str, Document] = {}
    for doc_text, meta in zip(got["documents"], got["metadatas"]):
        content = meta.get("parent_content", doc_text)
        if content not in seen:
            seen[content] = Document(
                page_content=content,
                metadata={"source": meta.get("source", "unknown"), "section": meta.get("section", "unknown")},
            )
    return list(seen.values())


def _generate_questions(llm: ChatGroq, chunk_content: str, source: str) -> list[str]:
    prompt = QUESTION_GENERATION_PROMPT.format(chunk_content=chunk_content, n=QUESTIONS_PER_CHUNK, source=source)
    response = llm.bind(temperature=LLM_TEMPERATURE).invoke([HumanMessage(content=prompt)])
    lines = [line.strip() for line in response.content.strip().split("\n") if line.strip()]
    return lines[:QUESTIONS_PER_CHUNK]


def build_hype_vectorstore() -> Chroma:
    if HYPE_VECTORSTORE_DIR.exists():
        shutil.rmtree(HYPE_VECTORSTORE_DIR)
        print(f"Deleted existing HyPE vector store at {HYPE_VECTORSTORE_DIR}")
    HYPE_VECTORSTORE_DIR.mkdir(parents=True)

    print(f"Loading embedding model ({EMBEDDING_MODEL_NAME})...")
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME, model_kwargs={"device": "cpu"})

    print(f"Loading LLM ({LLM_MODEL_NAME}, temperature={LLM_TEMPERATURE}) for question generation...")
    llm = ChatGroq(model=LLM_MODEL_NAME, api_key=os.environ["GROQ_API_KEY"])

    chunks = _load_unique_chunks(embeddings)
    print(f"\n{len(chunks)} unique chunks loaded from main vector store.")

    question_docs: list[Document] = []
    for i, chunk in enumerate(chunks, 1):
        questions = _generate_questions(llm, chunk.page_content, chunk.metadata["source"])
        print(f"  [{i}/{len(chunks)}] {chunk.metadata['source']} / {chunk.metadata['section'][:40]!r} "
              f"-> {len(questions)} questions")
        for q in questions:
            question_docs.append(Document(
                page_content=q,
                metadata={
                    "source": chunk.metadata["source"],
                    "section": chunk.metadata["section"],
                    "parent_content": chunk.page_content,
                },
            ))

    print(f"\nTotal question embeddings: {len(question_docs)}")
    print("Building HyPE ChromaDB vector store...")
    vectorstore = Chroma.from_documents(
        documents=question_docs,
        embedding=embeddings,
        persist_directory=str(HYPE_VECTORSTORE_DIR),
        collection_metadata={"hnsw:space": "cosine"},
    )
    print(f"HyPE vector store saved to {HYPE_VECTORSTORE_DIR}")

    metadata = {
        "embedding_model": EMBEDDING_MODEL_NAME,
        "llm_model": LLM_MODEL_NAME,
        "llm_temperature": LLM_TEMPERATURE,
        "questions_per_chunk": QUESTIONS_PER_CHUNK,
        "source_chunk_count": len(chunks),
        "question_embedding_count": len(question_docs),
        "hnsw_space": "cosine",
        "git_commit": _git_commit(),
        "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (HYPE_VECTORSTORE_DIR / "_ingestion_metadata.json").write_text(json.dumps(metadata, indent=2))
    print(f"HyPE ingestion metadata saved to {HYPE_VECTORSTORE_DIR / '_ingestion_metadata.json'}")

    return vectorstore


if __name__ == "__main__":
    build_hype_vectorstore()
