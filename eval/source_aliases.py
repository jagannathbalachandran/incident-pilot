"""Source-filename aliases for cross-corpus relevance checking.

Qrels hardcode expected_source as a single filename (e.g.
"checkout-api-runbook.md"). That's a fine assumption when every corpus uses
the same filenames -- but synthetic-data/latest_runbooks/ deliberately
reuses the SAME content under DIFFERENT filenames/formats (e.g.
checkout_api_runbook.pdf instead of checkout-api-runbook.md), to simulate
format diversity. Without this, a qrel's exact-string source check would
count a chunk as "wrong document" purely because of a filename/extension
difference, even when the actual content retrieved is correct -- confirmed
directly: querying the latest_runbooks HyPE index for a checkout-api query
returned checkout_api_runbook.pdf at 3 of 6 ranks, but recall scored 0.000
because "checkout_api_runbook.pdf" != "checkout-api-runbook.md".

Maps every known alternate filename to the canonical name qrels use, so
matches_expected_source() can compare on service identity, not literal
filename.
"""

SOURCE_ALIASES: dict[str, str] = {
    "checkout_api_runbook.pdf": "checkout-api-runbook.md",
    "payment_service_errors.pdf": "payment-service-runbook.md",
}


def matches_expected_source(actual_source: str, expected_source: str) -> bool:
    canonical = SOURCE_ALIASES.get(actual_source, actual_source)
    return canonical == expected_source
