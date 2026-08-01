"""Single-source RAG retrieval eval: single query, top-3, no HyDE.

Named "single-source" deliberately: every qrel in rag_qrels.py assumes the
relevant information for a query lives in exactly ONE document
(QrelItem.expected_source is a single string, not a list). That assumption
has a direct consequence for Precision@3 -- with only one genuinely
relevant chunk possible anywhere in the corpus for a given query, the
maximum achievable precision at k=3 is mathematically 1/3, no matter how
good retrieval is. A precision of 0.333 here means "found the one thing
there was to find," not "only a third right" -- Recall and MRR are the
metrics that carry real signal in this eval; Precision is reported mainly
as a sanity check that slots aren't being wasted on literal duplicates
(see the dedup-by-content note in evaluate_query below).

A future multi-source variant (queries whose correct answer legitimately
spans more than one document, e.g. a cascading incident touching two
services' runbooks) would need a different qrel shape and a different
precision ceiling -- not covered by this script.

Loads the persisted ChromaDB vector store directly (the same store
src/ingestion.py builds) and runs each query in rag_qrels.py through
similarity_search_with_score(query, k=3) -- the same k as CHUNKS_PER_QUERY
used per-sub-query inside IncidentPilot._retrieve_with_queries, but without
HyDE expansion, dedup, or reranking, so raw retrieval quality can be
assessed in isolation from those later pipeline stages.

Usage:
    uv run python eval/eval_retrieval_single_source.py
"""

import sys
from pathlib import Path

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from rag_qrels import QRELS
from schemas import QrelItem, QueryEvalResult, RagEvalReport, RetrievedChunk
from source_aliases import matches_expected_source

REPO_ROOT = Path(__file__).parent.parent
VECTORSTORE_DIR = REPO_ROOT / "synthetic-data" / "vectorstore"
K = 3


def load_vectorstore() -> Chroma:
    if not VECTORSTORE_DIR.exists():
        sys.exit(
            f"No vector store at {VECTORSTORE_DIR} -- "
            "run `uv run python src/ingestion.py` first."
        )
    embeddings = HuggingFaceEmbeddings(
        model_name="all-MiniLM-L6-v2",
        model_kwargs={"device": "cpu"},
    )
    return Chroma(persist_directory=str(VECTORSTORE_DIR), embedding_function=embeddings)


def evaluate_query(vectorstore: Chroma, qrel: QrelItem) -> QueryEvalResult:
    hits = vectorstore.similarity_search_with_score(qrel.query, k=K)

    # A chunk that was split for length carries the full pre-split text in
    # parent_content -- use that instead of the (possibly incomplete) piece
    # that was actually embedded, so relevance is checked against what would
    # really be returned to the LLM.
    retrieved = [
        RetrievedChunk(
            source=doc.metadata.get("source", "unknown"),
            section=doc.metadata.get("section", "unknown"),
            content=doc.metadata.get("parent_content", doc.page_content),
            score=score,
            rank=rank,
        )
        for rank, (doc, score) in enumerate(hits, start=1)
    ]

    # Relevance requires BOTH: right document AND a must_contain phrase in
    # its content. Source is checked first -- a phrase match in a chunk from
    # the wrong doc (e.g. another runbook's identical template wording)
    # must not count, even if the phrase itself matches.
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

    # Count DISTINCT relevant content, not distinct ranks. A chunk that was
    # safety-split into multiple pieces can have more than one sibling piece
    # land in the top-k -- each gets substituted back to the same
    # parent_content, so two "hits" can be the exact same text returned
    # twice. That's not two relevant chunks, it's one chunk occupying two
    # retrieval slots; counting it twice would inflate precision without
    # the SRE actually getting any additional distinct information.
    relevant_contents = {
        chunk.content for chunk in retrieved
        if matches_expected_source(chunk.source, qrel.expected_source)
        and any(phrase in chunk.content for phrase in qrel.must_contain)
    }
    relevant_retrieved = len(relevant_contents)

    return QueryEvalResult(
        query=qrel.query,
        expected_source=qrel.expected_source,
        retrieved=retrieved,
        matched_phrases=matched_phrases,
        precision=relevant_retrieved / K,
        recall=len(matched_phrases) / len(qrel.must_contain),
        reciprocal_rank=(1 / first_relevant_rank) if first_relevant_rank else 0.0,
    )


def print_report(report: RagEvalReport) -> None:
    print(f"RAG Retrieval Eval -- single-source, top-{report.k}, no HyDE\n")
    print(
        "Each query's relevant info is expected in exactly ONE document, so\n"
        f"Precision@{report.k} is mathematically capped at 1/{report.k} "
        f"(={1 / report.k:.3f}) when only one\n"
        "genuinely relevant chunk exists anywhere in the corpus -- hitting that\n"
        "number means retrieval found everything there was to find, not \"only\n"
        "a third right\". Recall and MRR are the metrics that carry real signal\n"
        "here; Precision mainly checks that slots aren't wasted on duplicates.\n"
    )

    query_w = 55
    header = f"{'Query':<{query_w}} {'Precision':>10} {'Recall':>8} {'RR':>8}"
    print(header)
    print("-" * len(header))
    for r in report.results:
        q = r.query if len(r.query) <= query_w else r.query[: query_w - 3] + "..."
        print(f"{q:<{query_w}} {r.precision:>10.3f} {r.recall:>8.3f} {r.reciprocal_rank:>8.3f}")
    print("-" * len(header))
    print(f"{'Mean / MRR':<{query_w}} {report.mean_precision:>10.3f} {report.mean_recall:>8.3f} {report.mrr:>8.3f}")


def main() -> RagEvalReport:
    vectorstore = load_vectorstore()
    results = [evaluate_query(vectorstore, qrel) for qrel in QRELS]

    n = len(results)
    report = RagEvalReport(
        k=K,
        results=results,
        mean_precision=sum(r.precision for r in results) / n,
        mean_recall=sum(r.recall for r in results) / n,
        mrr=sum(r.reciprocal_rank for r in results) / n,
    )

    print_report(report)
    return report


if __name__ == "__main__":
    main()
