"""HyPE retrieval + scoring for the benchmark harness.

No LLM call at query time -- the engineer's raw query is embedded directly
(same MiniLM model src/hype_ingestion.py used for the precomputed question
embeddings) and matched against those questions. A hit's metadata carries
parent_content (the real chunk text the question was generated from, not
the question itself), substituted back in the same way the main pipeline
substitutes a split chunk's parent_content.

Scoring reuses eval_retrieval_single_source.evaluate_query's exact
relevance rules (source match + must_contain phrase check + distinct-
content precision denominator) -- only which chunks get ranked into the
top-K differs, not how relevance is judged, so this is directly comparable
to every other mode in the benchmark.
"""

import sys
from pathlib import Path

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from schemas import QrelItem, QueryEvalResult, RetrievedChunk  # noqa: E402
from source_aliases import matches_expected_source  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HYPE_VECTORSTORE_DIR = REPO_ROOT / "synthetic-data" / "vectorstore_hype"

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"  # must match src/hype_ingestion.py
K = 6  # matches incident_pilot.MAX_RETRIEVED_CHUNKS (hyde's cap), for a fair comparison


def load_hype_vectorstore(vectorstore_dir: Path = HYPE_VECTORSTORE_DIR) -> Chroma:
    if not vectorstore_dir.exists():
        sys.exit(f"No HyPE vector store at {vectorstore_dir} -- run the matching src/hype_ingestion.py invocation first.")
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME, model_kwargs={"device": "cpu"})
    return Chroma(persist_directory=str(vectorstore_dir), embedding_function=embeddings)


def evaluate_query_hype(vectorstore: Chroma, qrel: QrelItem) -> QueryEvalResult:
    # Fetch more than K raw hits -- several questions can map to the same
    # parent chunk, so K raw hits could dedupe down to fewer than K unique
    # chunks. Worst case needs K * questions-per-chunk (3) raw hits to
    # guarantee K unique chunks; doubled for margin, on a 66-embedding corpus
    # this is still cheap.
    hits = vectorstore.similarity_search_with_score(qrel.query, k=K * 6)

    best_by_content: dict[str, tuple[float, dict]] = {}
    for doc, score in hits:
        content = doc.metadata.get("parent_content", doc.page_content)
        existing = best_by_content.get(content)
        if existing is None or score < existing[0]:
            best_by_content[content] = (score, doc.metadata)
    ranked = sorted(best_by_content.items(), key=lambda kv: kv[1][0])[:K]

    retrieved = [
        RetrievedChunk(
            source=meta.get("source", "unknown"),
            section=meta.get("section", "unknown"),
            content=content,
            score=score,
            rank=rank,
        )
        for rank, (content, (score, meta)) in enumerate(ranked, start=1)
    ]

    matched_phrases: list[str] = []
    first_relevant_rank = None
    for chunk in retrieved:
        if not matches_expected_source(chunk.source, qrel.expected_source):
            continue
        for phrase in qrel.must_contain:
            if phrase in chunk.content and phrase not in matched_phrases:
                matched_phrases.append(phrase)
                if first_relevant_rank is None:
                    first_relevant_rank = chunk.rank

    relevant_contents = {
        chunk.content for chunk in retrieved
        if matches_expected_source(chunk.source, qrel.expected_source)
        and any(phrase in chunk.content for phrase in qrel.must_contain)
    }

    return QueryEvalResult(
        query=qrel.query,
        expected_source=qrel.expected_source,
        retrieved=retrieved,
        matched_phrases=matched_phrases,
        precision=len(relevant_contents) / K,
        recall=len(matched_phrases) / len(qrel.must_contain),
        reciprocal_rank=(1 / first_relevant_rank) if first_relevant_rank else 0.0,
    )
