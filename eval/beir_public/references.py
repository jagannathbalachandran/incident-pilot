"""Published BEIR reference scores, so a result file says whether its own
number is good or bad without anyone reading git history.

Single source of truth, imported by both run_beir_eval.py and
exact_search_diag.py -- the figures were previously duplicated across the two
and recorded nowhere in the results themselves.

IMPORTANT -- references use EXACT search, our benchmark does not.
BEIR's standard dense retrieval is brute force: DenseRetrievalExactSearch
builds the full query x corpus score matrix and takes torch.topk over it (BEIR
ships a separate faiss_search.py for approximate retrieval, which is not the
standard path). run_beir_eval.py deliberately retrieves through Chroma's HNSW
index instead, because that is production's real path -- so a deviation from
these references is EXPECTED, and mostly is not a defect.

How much of it is the index was measured directly: on FiQA, exact search over
the identical embeddings scores 0.3687 against this table's 0.3690, while
Chroma's HNSW scores 0.3626. So ~95% of that deviation is ANN approximation
loss and ~0% is anything else. See exact_search_diag.py. ANN loss grows with
corpus size, which is why SciFact (5,183 docs) shows almost none and FiQA
(57,638) shows 1.7%.

Keyed by (dataset, model) so that a --model override run, which has no
published reference for that model/dataset pair, records null instead of being
silently compared against the wrong baseline.
"""

# (dataset, embedding model) -> published reference
PUBLISHED_REFERENCES: dict[tuple[str, str], dict] = {
    ("scifact", "all-MiniLM-L6-v2"): {
        "ndcg_at_10": 0.645,
        "search": "exact",
        "source": (
            "all-MiniLM-L6-v2 on BEIR SciFact; corroborated by two independent "
            "sources -- https://github.com/NikosMav/scifact-hybrid-retrieval and "
            "Superlinked's aggregated sentence-transformers benchmark table "
            "(https://superlinked.com/models/sentence-transformers-all-minilm-l6-v2)"
        ),
    },
    ("fiqa", "all-MiniLM-L6-v2"): {
        "ndcg_at_10": 0.369,
        "search": "exact",
        "source": (
            "all-MiniLM-L6-v2 on BEIR FiQA2018, Superlinked's aggregated "
            "sentence-transformers benchmark table "
            "(https://superlinked.com/models/sentence-transformers-all-minilm-l6-v2). "
            "The same table lists SciFact 0.645, which matches the independently "
            "cited SciFact figure above -- that agreement is what makes this one "
            "trustworthy. Note a web-search snippet for this pair reported 0.1503, "
            "which is wrong; the table itself reads 0.369."
        ),
    },
}

# Our own HNSW+L2 measurements, for diagnostics that need to attribute a
# deviation rather than just report one. Sourced from
# results/<dataset>__prod_config__*.json.
HNSW_L2_MEASURED: dict[tuple[str, str], float] = {
    ("scifact", "all-MiniLM-L6-v2"): 0.6451,
    ("fiqa", "all-MiniLM-L6-v2"): 0.3626,
}


def get_reference(dataset: str, model: str) -> dict | None:
    """Published reference for this (dataset, model), or None if none is known.

    Returning None rather than a best guess is deliberate: comparing a
    deliberately-weak override model (the GloVe fluke check) against
    all-MiniLM-L6-v2's reference would manufacture a meaningless "deviation".
    """
    return PUBLISHED_REFERENCES.get((dataset, model.split("/")[-1]))


def deviation(ours: float, reference: dict | None) -> dict | None:
    """Signed deviation of our score from the published reference.

    Negative means we scored below it -- the expected direction, since we
    retrieve through an approximate index and the references use exact search.
    """
    if reference is None or ours is None:
        return None
    ref = reference["ndcg_at_10"]
    return {
        "absolute": round(ours - ref, 4),
        "relative_pct": round((ours - ref) / ref * 100, 2),
        "note": (
            "Negative is expected: we retrieve via Chroma's approximate HNSW index, "
            "the reference uses exact search. See exact_search_diag.py, which "
            "attributes ~95% of FiQA's deviation to ANN loss."
        ),
    }
