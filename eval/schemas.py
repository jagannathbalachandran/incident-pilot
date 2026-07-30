"""Pydantic schemas for RAG retrieval evaluation.

Task 1 scope: single query -> similarity_search_with_score(k=3), no HyDE
expansion, no dedup/rerank across sub-queries. This isolates raw embedding
retrieval quality from the pipeline stages layered on top of it in
IncidentPilot._retrieve_with_queries.
"""

from pydantic import BaseModel, Field


class QrelItem(BaseModel):
    """One golden query and what counts as a relevant hit for it.

    Relevance is defined by content, not by a chunk's `section` metadata --
    SemanticChunker splits on meaning shifts, not `##` headers, so `section`
    (the chunk's first line) is not a stable identifier for what a chunk
    actually contains; a chunk can straddle two logical sections. A phrase
    in `must_contain` should be a substring distinctive enough that it only
    plausibly appears in the chunk(s) that should be retrieved for `query`.
    """

    query: str
    expected_source: str
    must_contain: list[str] = Field(
        description="Distinctive substrings. A retrieved chunk is relevant "
        "if any of these phrases appears in its content."
    )


class RetrievedChunk(BaseModel):
    source: str
    section: str
    content: str
    # None for chunks retrieved via IncidentPilot.retrieve() (the HyDE
    # pipeline) -- its public return type doesn't expose a similarity
    # score, unlike task-1's direct similarity_search_with_score() calls.
    score: float | None = None
    rank: int


class QueryEvalResult(BaseModel):
    query: str
    expected_source: str
    retrieved: list[RetrievedChunk]
    matched_phrases: list[str]
    precision: float
    recall: float
    reciprocal_rank: float


class RagEvalReport(BaseModel):
    k: int
    results: list[QueryEvalResult]
    mean_precision: float
    mean_recall: float
    mrr: float
