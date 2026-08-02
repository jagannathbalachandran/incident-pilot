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

# MAIN_VECTORSTORE_DIR is now built from latest_runbooks/ by default (see
# src/ingestion.py) -- HyPE's default source/output above needs no path
# change to follow that, since it just reads whatever's actually there.
LATEST_RUNBOOKS_VECTORSTORE_DIR = REPO_ROOT / "synthetic-data" / "vectorstore_latest_runbooks"
HYPE_LATEST_RUNBOOKS_VECTORSTORE_DIR = REPO_ROOT / "synthetic-data" / "vectorstore_hype_latest_runbooks"

# Legacy/reference corpus (all-markdown runbooks/, no format diversity) --
# built via `python src/ingestion.py --legacy-runbooks` first, then this
# file's --legacy-runbooks flag reads from there.
LEGACY_RUNBOOKS_VECTORSTORE_DIR = REPO_ROOT / "synthetic-data" / "vectorstore_legacy_runbooks"
HYPE_LEGACY_RUNBOOKS_VECTORSTORE_DIR = REPO_ROOT / "synthetic-data" / "vectorstore_hype_legacy_runbooks"

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

# doc_type-specific guidance slotted into QUESTION_GENERATION_PROMPT below --
# runbooks and postmortems are searched under different intents (live triage
# steps vs. "has this happened before"), so the questions generated for each
# should read differently even when both mention the same service/symptom.
RUNBOOK_QUESTION_GUIDANCE = """\
This chunk is from a RUNBOOK -- a prescriptive triage document engineers \
follow live, during an active incident. Bias your questions toward what an \
engineer asks while actively triaging: symptom identification ("why is X \
happening"), what to check next, mitigation/fix steps, and escalation \
criteria. Phrase them as an engineer would type them under time pressure, \
not as the runbook itself is written.\
"""

POSTMORTEM_QUESTION_GUIDANCE = """\
This chunk is from a POSTMORTEM -- a retrospective writeup of a past \
incident, referenced during triage to check whether the current symptoms \
match something that has happened before. Bias your questions toward that \
use case: "has this happened before", "what caused the outage where X \
happened", "what was the root cause of an incident like this", "what fixed \
it last time" -- not step-by-step triage instructions, since a postmortem \
doesn't give live triage steps.\
"""

DOC_TYPE_GUIDANCE = {
    "runbook": RUNBOOK_QUESTION_GUIDANCE,
    "postmortem": POSTMORTEM_QUESTION_GUIDANCE,
}

QUESTION_GENERATION_PROMPT = """\
You are indexing a runbook/postmortem chunk for a semantic search system \
used by on-call engineers during incident triage.

This chunk is from the document "{source}", which is about the \
**{service}** service. Even where the chunk mentions or cross-checks a \
*different* service (e.g. a step telling the on-call engineer to also \
check another service's dashboard), the questions you generate must stay \
anchored to what THIS document is fundamentally about. A cross-check step \
that mentions another service is not itself a question about that other \
service.

EVERY one of the {n} questions you generate MUST explicitly contain the \
literal identifier "{service}" -- do not write a question that only says \
"the service", "it", or leaves the service unnamed, even if the chunk's \
own wording doesn't repeat the service name on every line. This matters \
because these questions are matched against engineer queries by literal \
text similarity, not by shared context -- a question missing the service \
name will fail to match a query that includes it, even when the chunk is \
the right answer.
  Bad:  "Why is the error rate spiking on /login with 401 status code?"
  Good: "Why is {service}'s error rate spiking on /login with 401 status code?"

{doc_type_guidance}

Chunk content:
\"\"\"
{chunk_content}
\"\"\"

Generate exactly {n} questions an on-call engineer might type into this \
search system while actively triaging an incident, that this chunk would \
help answer -- phrased the way an engineer would actually ask under time \
pressure (symptom-focused, casual), not the way the chunk itself is \
written. Cover different angles where the chunk supports it -- but every \
question must be genuinely answerable from this specific chunk, not a \
generic incident question, and must not be mistakable for a question \
about a different service just because this chunk happens to mention one.

Return only the {n} questions, one per line. No numbering, no explanation.\
"""


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return "unknown"


def _load_unique_chunks(embeddings: HuggingFaceEmbeddings, source_vectorstore_dir: Path) -> list[Document]:
    """Unique chunks from the given source vector store, deduped by
    parent_content (same substitution rule as _retrieve_with_queries -- a
    chunk split for length has multiple embedded pieces sharing one
    parent_content; HyPE should generate questions per logical chunk, not
    per split piece)."""
    if not source_vectorstore_dir.exists():
        raise SystemExit(f"No vector store at {source_vectorstore_dir} -- run ingestion for it first.")

    vectorstore = Chroma(persist_directory=str(source_vectorstore_dir), embedding_function=embeddings)
    got = vectorstore.get()

    seen: dict[str, Document] = {}
    for doc_text, meta in zip(got["documents"], got["metadatas"]):
        content = meta.get("parent_content", doc_text)
        if content not in seen:
            seen[content] = Document(
                page_content=content,
                metadata={
                    "source": meta.get("source", "unknown"),
                    "section": meta.get("section", "unknown"),
                    "doc_type": meta["doc_type"],
                    "service": meta["service"],
                },
            )
    return list(seen.values())


def _generate_questions(llm: ChatGroq, chunk_content: str, source: str, doc_type: str, service: str) -> list[str]:
    prompt = QUESTION_GENERATION_PROMPT.format(
        chunk_content=chunk_content,
        n=QUESTIONS_PER_CHUNK,
        source=source,
        service=service,
        doc_type_guidance=DOC_TYPE_GUIDANCE[doc_type],
    )
    response = llm.bind(temperature=LLM_TEMPERATURE).invoke([HumanMessage(content=prompt)])
    lines = [line.strip() for line in response.content.strip().split("\n") if line.strip()]
    questions = lines[:QUESTIONS_PER_CHUNK]

    # Prompting alone doesn't reliably get a weaker model (llama-3.1-8b-instant)
    # to name the service in every question -- confirmed empirically (54% of
    # questions omitted it before this fix). Backstop deterministically rather
    # than just asking harder, so the property actually always holds.
    return [q if service.lower() in q.lower() else f"{q} ({service})" for q in questions]


def build_hype_vectorstore(
    source_vectorstore_dir: Path = MAIN_VECTORSTORE_DIR,
    hype_vectorstore_dir: Path = HYPE_VECTORSTORE_DIR,
    corpus_tag: str = "latest_runbooks+postmorterms",
) -> Chroma:
    """Defaults read the production vector store (built from latest_runbooks/
    by default, see src/ingestion.py) and write vectorstore_hype/. Pass a
    different source_vectorstore_dir/hype_vectorstore_dir/corpus_tag to
    build HyPE questions for a different corpus (e.g. the legacy all-markdown
    one) without touching the default one.
    """
    if hype_vectorstore_dir.exists():
        shutil.rmtree(hype_vectorstore_dir)
        print(f"Deleted existing HyPE vector store at {hype_vectorstore_dir}")
    hype_vectorstore_dir.mkdir(parents=True)

    print(f"Loading embedding model ({EMBEDDING_MODEL_NAME})...")
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME, model_kwargs={"device": "cpu"})

    print(f"Loading LLM ({LLM_MODEL_NAME}, temperature={LLM_TEMPERATURE}) for question generation...")
    llm = ChatGroq(model=LLM_MODEL_NAME, api_key=os.environ["GROQ_API_KEY"])

    chunks = _load_unique_chunks(embeddings, source_vectorstore_dir)
    print(f"\n{len(chunks)} unique chunks loaded from {source_vectorstore_dir.name}.")

    question_docs: list[Document] = []
    for i, chunk in enumerate(chunks, 1):
        questions = _generate_questions(
            llm, chunk.page_content, chunk.metadata["source"], chunk.metadata["doc_type"], chunk.metadata["service"]
        )
        print(f"  [{i}/{len(chunks)}] {chunk.metadata['source']} / {chunk.metadata['section'][:40]!r} "
              f"({chunk.metadata['doc_type']}, {chunk.metadata['service']}) -> {len(questions)} questions")
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
        persist_directory=str(hype_vectorstore_dir),
        collection_metadata={"hnsw:space": "cosine"},
    )
    print(f"HyPE vector store saved to {hype_vectorstore_dir}")

    metadata = {
        "corpus": corpus_tag,
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
    (hype_vectorstore_dir / "_ingestion_metadata.json").write_text(json.dumps(metadata, indent=2))
    print(f"HyPE ingestion metadata saved to {hype_vectorstore_dir / '_ingestion_metadata.json'}")

    return vectorstore


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--latest-runbooks":
        # Standalone comparison store, separate from the production one --
        # still useful for re-running the side-by-side eval later.
        build_hype_vectorstore(
            source_vectorstore_dir=LATEST_RUNBOOKS_VECTORSTORE_DIR,
            hype_vectorstore_dir=HYPE_LATEST_RUNBOOKS_VECTORSTORE_DIR,
            corpus_tag="latest_runbooks+postmorterms",
        )
    elif len(sys.argv) > 1 and sys.argv[1] == "--legacy-runbooks":
        build_hype_vectorstore(
            source_vectorstore_dir=LEGACY_RUNBOOKS_VECTORSTORE_DIR,
            hype_vectorstore_dir=HYPE_LEGACY_RUNBOOKS_VECTORSTORE_DIR,
            corpus_tag="runbooks+postmorterms",
        )
    else:
        build_hype_vectorstore()  # production: reads whatever's in vectorstore/ (latest_runbooks/ by default)
