"""Dense + BM25 hybrid retrieval, fused via reciprocal rank fusion (RRF).

Not a reimplementation of the production scoring logic in
eval_retrieval_single_source.evaluate_query -- reuses the exact same
relevance rules (source == expected_source, must_contain phrase check,
distinct-content precision denominator, parent_content substitution). Only
*which chunks get ranked into the top-K* differs: dense-only vs. dense+BM25
fused.

no_hyde: single raw query, dense+BM25 fused directly.
hyde: HyDE-expands the query into up to 6 queries (same _expand_query used
in production), fuses dense+BM25 per query, then keeps each chunk's BEST
(max) RRF score across the 6 queries -- mirroring
IncidentPilot._retrieve_with_queries' documented "keep each chunk's best
score across whichever queries matched it" behavior, just with an RRF score
in place of a raw distance.
"""

import re
from typing import Any

from rank_bm25 import BM25Okapi

from schemas import QrelItem, QueryEvalResult, RetrievedChunk

RRF_K = 60


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class HybridIndex:
    """Built once per benchmark run (in setup()), reused across every query."""

    def __init__(self, vectorstore):
        self.vectorstore = vectorstore
        got = vectorstore.get()
        self.docs: list[str] = got["documents"]
        self.metas: list[dict] = got["metadatas"]
        self.bm25 = BM25Okapi([_tokenize(d) for d in self.docs])

    def content(self, i: int) -> str:
        return self.metas[i].get("parent_content", self.docs[i])

    def source(self, i: int) -> str:
        return self.metas[i].get("source", "unknown")


def _dense_rank_map(index: HybridIndex, query: str) -> dict[str, int]:
    hits = index.vectorstore.similarity_search_with_score(query, k=len(index.docs))
    rank_of: dict[str, int] = {}
    for rank, (doc, _score) in enumerate(hits, 1):
        c = doc.metadata.get("parent_content", doc.page_content)
        if c not in rank_of:
            rank_of[c] = rank
    return rank_of


def _bm25_rank_map(index: HybridIndex, query: str) -> dict[str, int]:
    scores = index.bm25.get_scores(_tokenize(query))
    order = sorted(range(len(index.docs)), key=lambda i: -scores[i])
    rank_of: dict[str, int] = {}
    for rank, i in enumerate(order, 1):
        c = index.content(i)
        if c not in rank_of:
            rank_of[c] = rank
    return rank_of


def _rrf_scores_for_query(index: HybridIndex, query: str) -> dict[str, float]:
    dense_ranks = _dense_rank_map(index, query)
    bm25_ranks = _bm25_rank_map(index, query)
    all_contents = set(dense_ranks) | set(bm25_ranks)
    n = len(index.docs) + 1
    return {
        c: 1 / (RRF_K + dense_ranks.get(c, n)) + 1 / (RRF_K + bm25_ranks.get(c, n))
        for c in all_contents
    }


def _content_to_source(index: HybridIndex) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(len(index.docs)):
        out.setdefault(index.content(i), index.source(i))
    return out


def _build_result(qrel: QrelItem, ranked_contents: list[str], content_to_source: dict[str, str]) -> QueryEvalResult:
    retrieved = [
        RetrievedChunk(
            source=content_to_source.get(c, "unknown"),
            section="unknown",
            content=c,
            rank=rank,
        )
        for rank, c in enumerate(ranked_contents, start=1)
    ]

    matched_phrases: list[str] = []
    first_relevant_rank = None
    for chunk in retrieved:
        if chunk.source != qrel.expected_source:
            continue
        for phrase in qrel.must_contain:
            if phrase in chunk.content and phrase not in matched_phrases:
                matched_phrases.append(phrase)
                if first_relevant_rank is None:
                    first_relevant_rank = chunk.rank

    relevant_contents = {
        chunk.content for chunk in retrieved
        if chunk.source == qrel.expected_source
        and any(phrase in chunk.content for phrase in qrel.must_contain)
    }

    k = len(ranked_contents) or 1
    return QueryEvalResult(
        query=qrel.query,
        expected_source=qrel.expected_source,
        retrieved=retrieved,
        matched_phrases=matched_phrases,
        precision=len(relevant_contents) / k,
        recall=len(matched_phrases) / len(qrel.must_contain),
        reciprocal_rank=(1 / first_relevant_rank) if first_relevant_rank else 0.0,
    )


def evaluate_query_no_hyde_hybrid(index: HybridIndex, qrel: QrelItem, top_k: int = 3) -> QueryEvalResult:
    scores = _rrf_scores_for_query(index, qrel.query)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:top_k]
    content_to_source = _content_to_source(index)
    return _build_result(qrel, [c for c, _s in ranked], content_to_source)


def evaluate_query_hyde_hybrid(ctx: Any, qrel: QrelItem, top_k: int = 6) -> QueryEvalResult:
    pilot, index = ctx
    queries = pilot._expand_query(qrel.query)

    best_score: dict[str, float] = {}
    for q in queries:
        for c, score in _rrf_scores_for_query(index, q).items():
            if c not in best_score or score > best_score[c]:
                best_score[c] = score

    ranked = sorted(best_score.items(), key=lambda kv: -kv[1])[:top_k]
    content_to_source = _content_to_source(index)
    return _build_result(qrel, [c for c, _s in ranked], content_to_source)
