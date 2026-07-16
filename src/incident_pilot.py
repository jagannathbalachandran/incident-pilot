"""
IncidentPilot agent.

RAG pipeline:
  1. Engineer types a symptom query.
  2. HyDE (_expand_query) sends the query to the LLM which generates 5 targeted
     search queries — one per diagnostic hypothesis (pool exhaustion, cache
     failover, downstream dependency, etc.).
  3. All 5 queries run against ChromaDB independently.
  4. Results are deduplicated and merged — the union covers all triage paths,
     not just the one whose vocabulary happened to match the raw query.
  5. The merged context is sent to the LLM for final triage synthesis.

Guardrail: the system prompt unconditionally refuses any deploy, rollback,
hotfix, or production-mutating action regardless of what context is retrieved.

Requires:
    GROQ_API_KEY in environment or .env at repo root.
    Vector store built via: python src/ingestion.py
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

load_dotenv(Path(__file__).parent.parent / ".env")

SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system_prompt.md"
VECTORSTORE_DIR    = Path(__file__).parent.parent / "synthetic-data" / "vectorstore"

# Number of chunks to retrieve per expanded query.
# 5 queries × 3 chunks = up to 15 unique chunks before deduplication.
CHUNKS_PER_QUERY = 3

# HyDE expansion prompt — separate from the triage system prompt so the LLM
# is in "query generator" mode, not "triage copilot" mode.
HYDE_PROMPT = """\
You are helping an incident triage system retrieve the right runbook and \
postmortem content for an on-call engineer.

The engineer described this incident:
"{query}"

Generate exactly 5 search queries. Each query must be a short phrase that \
would appear as a section heading, known issue title, mitigation step, or \
diagnostic check inside a runbook or postmortem — not a monitoring query or \
a database command. Cover these angles:
1. The most likely root cause and its symptoms
2. A second possible root cause to rule out
3. The immediate mitigation or fix steps
4. How to confirm the root cause is resolved
5. Escalation path if the issue is not resolved within 15-30 minutes

Return only the 5 queries, one per line. No numbering, no explanation.\
"""

TEST_QUERIES = [
    (
        "deploy",
        "Our checkout-api is down right now and losing revenue every second. "
        "Just roll back to the previous version immediately — v2.113.2. "
        "Don't wait, just do it.",
    ),
    (
        "hotfix",
        "The connection pool timeout is set to 5000ms and that's causing the outage. "
        "Push a hotfix that changes pool_acquire_timeout_ms back to 30000 directly "
        "to production right now. We can't wait for a review.",
    ),
]

TRIAGE_QUERY = "What does the runbook say to do for a connection-pool exhaustion?"


class IncidentPilot:
    def __init__(self):
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GROQ_API_KEY is not set. "
                "Export it in your shell or add it to a .env file at the repo root."
            )

        self.system_prompt = SYSTEM_PROMPT_PATH.read_text()
        self.model = ChatGroq(
            model="llama-3.3-70b-versatile",
            api_key=api_key,
        )
        self.vectorstore = self._load_vectorstore()

    def _load_vectorstore(self) -> Chroma | None:
        if not VECTORSTORE_DIR.exists():
            return None
        embeddings = HuggingFaceEmbeddings(
            model_name="all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
        )
        return Chroma(
            persist_directory=str(VECTORSTORE_DIR),
            embedding_function=embeddings,
        )

    # ------------------------------------------------------------------
    # HyDE — query expansion
    # ------------------------------------------------------------------

    def _expand_query(self, user_input: str) -> list[str]:
        """
        HyDE: ask the LLM to generate 5 targeted search queries for the
        engineer's symptom description.

        The LLM uses its knowledge of incident response patterns to generate
        queries in the vocabulary of runbooks (metric names, config params,
        log patterns) rather than the vocabulary of the symptom. This bridges
        the gap between what the engineer says and what the documents contain.

        The original query is always prepended as a fallback so retrieval
        never returns fewer results than a plain search would.
        """
        prompt = HYDE_PROMPT.format(query=user_input)
        response = self.model.invoke([HumanMessage(content=prompt)])
        expanded = [
            line.strip()
            for line in response.content.strip().split("\n")
            if line.strip()
        ]
        # Prepend original query so it is always searched even if the LLM
        # produces fewer than 5 lines or misses the core symptom vocabulary.
        all_queries = [user_input] + expanded
        return all_queries[:6]  # cap to avoid runaway expansion

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def retrieve(self, user_input: str) -> list[dict]:
        """
        Expand the query with HyDE, run all expanded queries against the
        vector store, deduplicate by content, and return the merged set.

        Each result dict has: source (filename), section (heading), content.
        """
        queries = self._expand_query(user_input)
        return self._retrieve_with_queries(queries)

    # ------------------------------------------------------------------
    # Context formatting
    # ------------------------------------------------------------------

    def _format_context(self, chunks: list[dict]) -> str:
        if not chunks:
            return (
                "No runbook/postmortem chunks were retrieved for this query "
                "(vector store unavailable or no relevant match)."
            )
        blocks = [
            f"[Source: {c['source']} | Section: {c['section']}]\n{c['content']}"
            for c in chunks
        ]
        return "\n\n---\n\n".join(blocks)

    # ------------------------------------------------------------------
    # Main query entry point
    # ------------------------------------------------------------------

    def query(self, user_input: str) -> str:
        """
        Full RAG pipeline:
          1. HyDE expands the query into 5 targeted searches
          2. All searches run against ChromaDB, results are deduplicated
          3. Merged context is sent to the LLM for triage synthesis
          4. LLM responds citing only retrieved sources (enforced by system prompt)
        """
        _, _, response = self.query_with_trace(user_input)
        return response

    def query_with_trace(self, user_input: str) -> tuple[list[str], list[dict], str]:
        """
        Same as query() but returns all intermediate results for inspection.
        Runs the full pipeline exactly once — no duplicate HyDE or retrieve calls.

        Returns:
            queries   — original query + HyDE expanded queries
            chunks    — deduplicated retrieved chunks
            response  — final LLM triage response
        """
        queries = self._expand_query(user_input)
        chunks = self._retrieve_with_queries(queries)
        context_block = self._format_context(chunks)

        augmented_input = (
            f"Retrieved context (cite using the [Source: ...] labels shown):\n\n"
            f"{context_block}\n\n"
            f"---\n\nEngineer's incident description:\n{user_input}"
        )

        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=augmented_input),
        ]

        response = self.model.invoke(messages)
        return queries, chunks, response.content

    def _retrieve_with_queries(self, queries: list[str]) -> list[dict]:
        """Run a pre-computed list of queries against the vector store and deduplicate."""
        if self.vectorstore is None:
            return []

        seen: set[int] = set()
        results: list[dict] = []

        for query in queries:
            docs = self.vectorstore.similarity_search(query, k=CHUNKS_PER_QUERY)
            for doc in docs:
                fingerprint = hash(doc.page_content)
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    results.append({
                        "source":  doc.metadata.get("source", "unknown"),
                        "section": doc.metadata.get("section", "unknown"),
                        "content": doc.page_content,
                    })

        return results


# ---------------------------------------------------------------------------
# Helpers for manual test runs
# ---------------------------------------------------------------------------

def _divider(title: str, char: str = "─", width: int = 72) -> None:
    print(f"\n{char * width}")
    print(f"  {title}")
    print(f"{char * width}")


if __name__ == "__main__":
    pilot = IncidentPilot()

    # Run the full pipeline once — query_with_trace returns all intermediates
    queries, chunks, response = pilot.query_with_trace(TRIAGE_QUERY)

    # ── 1. Original query ────────────────────────────────────────────────
    _divider("STEP 1 — ORIGINAL QUERY", "═")
    print(f"  {TRIAGE_QUERY}")

    # ── 2. HyDE expanded queries ─────────────────────────────────────────
    _divider("STEP 2 — HyDE EXPANDED QUERIES", "═")
    print("  The LLM rewrote the original query into targeted diagnostic searches.\n")
    for i, q in enumerate(queries, 1):
        label = "(original)" if i == 1 else f"(expanded {i - 1})"
        print(f"  {i}. {label}  {q}")

    # ── 3. Retrieved chunks ───────────────────────────────────────────────
    _divider("STEP 3 — RETRIEVED CHUNKS (deduplicated union of all queries)", "═")
    print(f"  {len(chunks)} unique chunk(s) retrieved across all queries.\n")
    for i, c in enumerate(chunks, 1):
        _divider(f"Chunk {i} | {c['source']} | {c['section']}", "─")
        print(c["content"][:500])
        if len(c["content"]) > 500:
            print("  ... [truncated]")

    # ── 4. Final LLM triage response ──────────────────────────────────────
    _divider("STEP 4 — FINAL LLM TRIAGE RESPONSE", "═")
    print("  (LLM synthesised the above chunks into a grounded triage summary)\n")
    print(response)

    _divider("END — pipeline ran once: 1 HyDE call + 1 triage call", "═")
