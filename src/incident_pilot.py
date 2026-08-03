"""
IncidentPilot agent — Week 1, Task 3 + Task 8 + Task 13 (MCP tool calling).

Initialises ChatGroq with the triage-copilot system prompt, retrieves
grounding chunks from the RAG vector store, and lets the model itself decide
whether/which of two MCP-backed tools (``query_metrics``, ``query_logs``) to
call before producing a cited triage summary. Tool calls are a real MCP
round trip to ``mcp_server/server.py`` (spawned once, over stdio) -- not a
same-process function call.

RAG retrieval uses HyDE query expansion (``_expand_query``): the engineer's
raw symptom description is sent to the LLM, which generates 3 targeted
search queries in the vocabulary runbooks/postmortems actually use (metric
names, config params, known-issue titles) rather than the engineer's
vocabulary -- each one required to explicitly name the service the incident
is about, if identifiable, since a query missing the service name can
retrieve a different service's runbook. All 4 queries (3 expanded + the
original) run against ChromaDB independently and the results are
deduplicated by content -- the union covers multiple triage hypotheses, not
just whichever one lexically matched the raw query.

Running this file directly fires a grounded triage query against the
connection-pool-exhaustion runbook.

Requires:
    GROQ_API_KEY environment variable (or a .env file at the repo root).
    A vector store built via ``python src/ingestion.py``.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

from logging_config import setup_logging
from mcp_client import MCPClient
from observability import configure_observability, track, update_trace_metadata
from request_context import get_request_id

logger = logging.getLogger(__name__)

# Load .env from repo root so GROQ_API_KEY is available without a shell export
load_dotenv(Path(__file__).parent.parent / ".env")

# Safety cap on tool-calling rounds per query, so a model that keeps
# requesting tools can't loop forever.
MAX_TOOL_ROUNDS = 3

# Priority-1 guardrail (prompts/system_prompt.md) says the model must refuse
# deploy/rollback/hotfix-style requests without calling a tool or analyzing
# data. Relying on the prompt alone isn't enough -- a bound tool can still
# get invoked (or malformed-called) against that instruction. This is a
# code-level backstop: for messages matching these action verbs, tools are
# never bound to that call at all, so no tool call is *possible*, not just
# discouraged.
_ACTION_REQUEST_PATTERN = re.compile(
    r"\b(deploy|roll[\s-]?back|hotfix|release|restart|merge|scale|drain|"
    r"terminate|push(?:ing)?\s+(?:a\s+)?(?:fix|change|update)|"
    r"change\s+(?:the\s+)?config|apply\s+(?:a\s+|the\s+)?config)\b",
    re.IGNORECASE,
)


def _looks_like_action_request(text: str) -> bool:
    return bool(_ACTION_REQUEST_PATTERN.search(text))


def _build_tools(mcp_client: MCPClient) -> list[StructuredTool]:
    """Build LangChain tool wrappers around the two MCP-backed telemetry
    tools. Each wrapper's body is just a real MCP ``call_tool`` round trip
    to ``mcp_server/server.py`` -- no telemetry logic lives here.

    Docstrings below become the tool descriptions the model actually reads
    to decide whether/which tool to call -- keep them accurate.
    """

    def query_metrics(service: Optional[str] = None, timeframe: str = "15m") -> dict:
        """Query live Prometheus metrics: p99 latency, error rate, active
        connections, and cache hit ratio. If Prometheus is unreachable,
        returns ``source: "unavailable"`` with no data -- report this to
        the engineer as "unable to reach Prometheus" rather than
        substituting stale data.

        Args:
            service: Service name to scope to (e.g. "checkout-api"). Omit
                to query across every simulated service at once.
            timeframe: Relative window like "15m" or "1h". Defaults to the
                last 15 minutes.
        """
        return mcp_client.call_tool(
            "query_metrics", {"service": service, "timeframe": timeframe}
        )

    def query_logs(service: Optional[str] = None, timeframe: str = "15m") -> dict:
        """Query application logs and return a structured analysis: log
        level breakdown, top recurring message patterns, error clusters,
        and reconstructed user-journey traces (login -> ... -> logout).
        If Loki is unreachable, returns ``source: "unavailable"`` with no
        data -- report this to the engineer as "unable to reach Loki"
        rather than substituting stale data.

        Args:
            service: Service name to scope to. Omit to query across every
                simulated service (needed to reconstruct a full journey,
                since its spans land in more than one service's log stream).
            timeframe: Relative window like "15m" or "1h". Defaults to the
                last 15 minutes.
        """
        return mcp_client.call_tool(
            "query_logs", {"service": service, "timeframe": timeframe}
        )

    return [
        StructuredTool.from_function(func=query_metrics, name="query_metrics"),
        StructuredTool.from_function(func=query_logs, name="query_logs"),
    ]

SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system_prompt.md"
VECTORSTORE_DIR = Path(__file__).parent.parent / "synthetic-data" / "vectorstore"

# Chunks retrieved per HyDE-expanded query. 4 queries (3 expanded + original)
# x 3 chunks = up to 12 raw hits before dedup.
CHUNKS_PER_QUERY = 3

# Hard cap on chunks actually sent to the LLM: the top-N by similarity
# score after the HyDE union/dedup below, not a raw truncation. Needed
# regardless of CHUNKS_PER_QUERY: an uncapped RAG block (up to 18 raw hits,
# commonly 12-15 unique after dedup) plus the system prompt and tool
# schemas reliably exceeds llama-3.1-8b-instant's 6000 tokens-per-minute
# ceiling on the very first call, before any tool round trip -- this bounds
# it while still keeping the multi-angle HyDE search itself unrestricted.
MAX_RETRIEVED_CHUNKS = 6

# HyDE expansion should be deterministic for reproducible retrieval evals --
# see _expand_query. Named here (not just inlined at the call site) so
# eval/benchmark/registry.py can read the actual value into pipeline_config
# instead of duplicating it as a separate hardcoded number.
HYDE_TEMPERATURE = 0

# HyDE expansion prompt -- separate from the triage system prompt so the LLM
# is in "query generator" mode, not "triage copilot" mode.
HYDE_PROMPT = """\
You are helping an incident triage system retrieve the right runbook and \
postmortem content for an on-call engineer.

The engineer described this incident:
"{query}"

First, identify which service this incident is about, if the description \
names or clearly implies one (e.g. "payment-service", "checkout-api", \
"listing-service", "auth-service", "cart-api"). If a service is \
identifiable, EVERY query you generate below must explicitly include that \
service's name -- do not generate a query that only says "the service" or \
drops the name partway through. This matters because these queries are \
matched against runbook/postmortem text by literal similarity, not shared \
context -- a query missing the service name can retrieve a different \
service's runbook instead of the right one. If no service is identifiable \
from the description, generate the queries without inventing one.

Generate exactly 3 search queries. Each query must be a short phrase that \
would appear as a section heading, known issue title, or diagnostic check \
inside a runbook or postmortem -- not a monitoring query or a database \
command. Cover these angles:
1. The most likely root cause and its symptoms
2. A second possible root cause to rule out
3. The immediate mitigation or fix steps

Return only the 3 queries, one per line. No numbering, no explanation.\
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

TRIAGE_QUERY = (
    "What does the runbook say to do for a connection-pool exhaustion?"
)


class IncidentPilot:
    """AI-powered incident-response copilot.

    Combines RAG (runbooks / postmortems) with live metrics (Prometheus /
    Loki) to produce cited triage summaries.  Never executes deploys,
    rollbacks, or production-mutating actions.
    """

    def __init__(self, vectorstore_dir: Path | None = None):
        """vectorstore_dir: override the default vector store location --
        e.g. for eval-only comparisons against a different corpus (see
        eval/benchmark/registry.py's *_latest_runbooks modes). Defaults to
        None, which keeps today's behavior (module-level VECTORSTORE_DIR)
        unchanged for every existing caller.
        """
        self._vectorstore_dir = vectorstore_dir or VECTORSTORE_DIR
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            logger.error("GROQ_API_KEY is not set")
            raise EnvironmentError(
                "GROQ_API_KEY is not set. "
                "Export it in your shell or add it to a .env file at the repo root."
            )

        # Model is configurable via GROQ_MODEL so you can flip between Groq
        # models (each has its own separate per-day token budget) without a
        # code change -- e.g. drop to llama-3.1-8b-instant when the 70b
        # model's daily quota is exhausted.
        model_name = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
        self._model_name = model_name
        logger.info("Initialising IncidentPilot with model=%s", model_name)
        self.system_prompt = SYSTEM_PROMPT_PATH.read_text()
        self.model = ChatGroq(
            model=model_name,
            api_key=api_key,
        )
        self.vectorstore = self._load_vectorstore()

        # MCP: spawn the telemetry tool server once, bind its tools onto the
        # model so the LLM itself decides whether/which to call per query.
        self.mcp_client = MCPClient()
        self.mcp_client.start()
        self.tools = _build_tools(self.mcp_client)
        self.model_with_tools = self.model.bind_tools(self.tools)

        # Observability: Phoenix's LangChainInstrumentor patches LangChain
        # globally (see configure_observability()), so every ChatGroq.invoke()
        # is auto-traced with no per-model callback needed here -- unlike some
        # other tracers. Only requires configure_observability() to have run
        # before the first real invoke(), not before construction.

        # Cache for trace data (exposed via get_trace() for the UI)
        self._last_trace: dict | None = None

        logger.info("IncidentPilot initialised (vectorstore=%s, tools=%s)",
                     "loaded" if self.vectorstore else "unavailable",
                     [t.name for t in self.tools])

    def close(self) -> None:
        """Shut down the MCP server subprocess. Not required for normal
        process exit (it's a daemon thread), but useful for tests/cleanup."""
        self.mcp_client.close()

    # ------------------------------------------------------------------
    # RAG retrieval
    # ------------------------------------------------------------------

    def _load_vectorstore(self) -> Chroma | None:
        if not self._vectorstore_dir.exists():
            logger.warning("Vector store directory not found at %s — RAG disabled",
                           self._vectorstore_dir)
            return None
        logger.debug("Loading embedding model (all-MiniLM-L6-v2)...")
        embeddings = HuggingFaceEmbeddings(
            model_name="all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
        )
        logger.debug("Opening ChromaDB at %s", self._vectorstore_dir)
        return Chroma(
            persist_directory=str(self._vectorstore_dir),
            embedding_function=embeddings,
        )

    @track(name="hyde_expansion")
    def _expand_query(self, user_input: str) -> list[str]:
        """HyDE: ask the LLM to generate 3 targeted search queries for the
        engineer's symptom description.

        The LLM uses its knowledge of incident-response patterns to generate
        queries in the vocabulary of runbooks (metric names, config params,
        log patterns) rather than the vocabulary of the symptom. This bridges
        the gap between what the engineer says and what the documents contain.

        The original query is always prepended as a fallback so retrieval
        never returns fewer results than a plain search would.

        temperature=0 here (not on self.model itself, which defaults to 0.7
        and is shared with the final answer/contradiction-check calls) --
        HyDE expansion should be deterministic for reproducible retrieval
        evals; the final answer's temperature is untouched.
        """
        prompt = HYDE_PROMPT.format(query=user_input)
        response = self.model.bind(temperature=HYDE_TEMPERATURE).invoke([HumanMessage(content=prompt)])
        expanded = [
            line.strip()
            for line in response.content.strip().split("\n")
            if line.strip()
        ]
        # Prepend original query so it is always searched even if the LLM
        # produces fewer than 3 lines or misses the core symptom vocabulary.
        all_queries = [user_input] + expanded
        return all_queries[:4]  # cap to avoid runaway expansion

    @track(type="retriever", name="rag_retrieval")
    def _retrieve_with_queries(self, queries: list[str]) -> list[dict]:
        """Run a pre-computed list of queries against the vector store,
        deduplicate by content (keeping each chunk's best -- lowest-distance
        -- score across every query it matched), rank the deduplicated set
        by that score, and return the top ``MAX_RETRIEVED_CHUNKS``.

        Straight truncation in query order would let the first query or two
        exhaust the cap, silently dropping chunks found by later HyDE
        angles (e.g. "escalation path") even when they're a better match
        than something found earlier -- ranking by actual similarity score
        picks the best chunks regardless of which query surfaced them.

        Each result dict has: source (filename), section (heading), content.
        """
        if self.vectorstore is None:
            logger.warning("_retrieve_with_queries() called but vectorstore is unavailable")
            return []

        # fingerprint -> (best_score, chunk_dict). Chroma's default distance
        # metric is L2 (lower = more similar), so "best" is the minimum.
        best_by_fingerprint: dict[int, tuple[float, dict]] = {}
        for query in queries:
            results = self.vectorstore.similarity_search_with_score(query, k=CHUNKS_PER_QUERY)
            for doc, score in results:
                # A chunk too long for the embedding model's token limit was
                # split at ingestion time; parent_content (if present) is
                # the full pre-split text, so this returns the whole
                # original section instead of a fragment that may be
                # missing its header or cut mid-instruction. Fingerprint on
                # this same value (not the raw sub-chunk) so sibling pieces
                # of one split parent collapse to a single entry instead of
                # each occupying a separate MAX_RETRIEVED_CHUNKS slot with
                # identical content.
                content = doc.metadata.get("parent_content", doc.page_content)
                fingerprint = hash(content)
                existing = best_by_fingerprint.get(fingerprint)
                if existing is None or score < existing[0]:
                    best_by_fingerprint[fingerprint] = (score, {
                        "source": doc.metadata.get("source", "unknown"),
                        "section": doc.metadata.get("section", "unknown"),
                        "content": content,
                    })

        ranked = sorted(best_by_fingerprint.values(), key=lambda pair: pair[0])
        total_unique = len(ranked)
        chunks = [chunk for _score, chunk in ranked[:MAX_RETRIEVED_CHUNKS]]

        logger.info("RAG retrieved %d unique chunk(s) across %d quer(y/ies), "
                     "ranked by similarity (top %d sent to LLM): %s",
                     total_unique, len(queries), len(chunks),
                     [f"{c['source']} / {c['section']}" for c in chunks])
        return chunks

    def retrieve(self, user_input: str) -> list[dict]:
        """Expand the query with HyDE, run all expanded queries against the
        vector store, and return the deduplicated merged set of grounding
        chunks as ``{source, section, content}`` dicts."""
        queries = self._expand_query(user_input)
        return self._retrieve_with_queries(queries)

    def _format_context(self, chunks: list[dict]) -> str:
        if not chunks:
            logger.debug("No RAG chunks to format — returning empty-context message")
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
    # MCP tool-calling loop
    # ------------------------------------------------------------------

    @track(type="tool", name="mcp_tool")
    def _call_mcp_tool(self, name: str, args: dict) -> dict:
        """Execute one MCP tool call and return its result dict, or an
        ``{"error": ...}`` dict if the call itself failed (e.g. the MCP
        server process died) -- this is surfaced to the LLM as a tool
        result like any other, rather than crashing the query."""
        try:
            result = self.mcp_client.call_tool(name, args)
            logger.info("Tool call %s(%s) -> source=%s", name, args, result.get("source"))
            return result
        except Exception as exc:
            logger.error("Tool call %s(%s) failed: %s", name, args, exc)
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Code-level contradiction detection
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_live_metrics(metrics: list, service: str = "checkout-api") -> dict:
        """Extract named metric values for one service from the condensed
        ``query_metrics`` tool result (a list of
        ``{"name", "service", "endpoint", "value"}`` dicts -- see
        ``mcp_server.server._condense_metrics``).

        Returns a dict like ``{"svc_p99_latency_ms": 1486, "svc_error_rate_pct": 4.8, ...}``.
        The contradiction-check machinery below is scoped to one service
        (``checkout-api`` by default, matching where the pool/cache/fraud
        scenarios default to) even though live queries now span every
        simulated service.
        """
        parsed: dict[str, float] = {}
        if not metrics:
            return parsed
        for entry in metrics:
            name = entry.get("name", "")
            if service and entry.get("service") != service:
                continue
            value = entry.get("value")
            if name and value is not None:
                try:
                    parsed[name] = float(value)
                except (ValueError, TypeError):
                    pass
        return parsed

    @staticmethod
    def _classify_data(m: dict) -> str:
        """Classify which incident the live data matches, based on known
        metric thresholds.

        Returns one of ``"pool"``, ``"cache"``, ``"fraud"``, or ``"normal"``.
        """
        error_rate = m.get("svc_error_rate_pct", 0)
        connections = m.get("svc_active_connections", 0)
        cache_hit = m.get("svc_cache_hit_ratio", 1.0)
        latency = m.get("svc_p99_latency_ms", 0)

        # Priority order: most distinctive signature first
        # Pool: high error rate + high connections
        if error_rate > 1.0 and connections > 150 and cache_hit > 0.90:
            return "pool"
        # Fraud: very high error rate + normal connections
        if error_rate > 8.0 and connections < 140 and cache_hit > 0.90:
            return "fraud"
        # Cache: low cache hit + low error rate
        if cache_hit < 0.60 and error_rate < 1.0:
            return "cache"
        # Fraud also possible with moderate error rate + normal connections
        if error_rate > 5.0 and connections < 140 and cache_hit > 0.90:
            return "fraud"
        return "normal"

    @staticmethod
    def _classify_user_query(query: str) -> str | None:
        """Detect which incident the user's question is asking about, based
        on keyword matching.

        Returns one of ``"pool"``, ``"cache"``, ``"fraud"``, or **None** if
        no specific incident is mentioned.
        """
        q = query.lower()
        keywords_pool = {"pool", "connection", "exhaustion", "max_connections"}
        keywords_cache = {"cache", "redis", "failover", "warming", "cache_hit"}
        keywords_fraud = {"fraud", "scoring", "503"}

        score_pool = sum(1 for kw in keywords_pool if kw in q)
        score_cache = sum(1 for kw in keywords_cache if kw in q)
        score_fraud = sum(1 for kw in keywords_fraud if kw in q)

        scores = [("pool", score_pool), ("cache", score_cache), ("fraud", score_fraud)]
        scores.sort(key=lambda x: -x[1])
        best_label, best_score = scores[0]
        return best_label if best_score >= 1 else None

    @staticmethod
    def _build_contradiction_text(data_class: str, query_class: str) -> str | None:
        """Build a contradiction warning paragraph if the data class does not
        match the query class.

        Returns a string like:
          ``[Contradiction] The live data suggests a pool exhaustion issue
          (elevated error rate, connections near max), but you asked about
          cache failover.``

        Returns **None** if there is no contradiction.
        """
        label_map = {
            "pool": "connection-pool exhaustion (elevated error rate, connections near max)",
            "cache": "cache failover (low cache hit ratio, normal error rate)",
            "fraud": "fraud-scoring outage (high error rate, normal connections)",
        }
        if data_class == "normal" or query_class is None:
            return None
        if data_class == query_class:
            return None

        data_desc = label_map.get(data_class, data_class)
        query_desc = label_map.get(query_class, query_class)
        return (
            f"[Contradiction] The live data suggests {data_desc}, "
            f"but you asked about {query_desc}. "
            f"Please verify your description against the metrics above."
        )

    @staticmethod
    def _detect_contradictions(
        user_input: str,
        logs_result: dict,
    ) -> str | None:
        """High-level check: parse metrics, classify both data and user query,
        and return a contradiction warning if they disagree.

        The returned string (if any) gets injected into the LLM prompt so
        the model has a hard factual signal that a mismatch exists.
        """
        metrics_raw = (logs_result or {}).get("metrics", [])
        if not metrics_raw:
            return None

        m = IncidentPilot._parse_live_metrics(metrics_raw)
        if not m:
            return None

        data_class = IncidentPilot._classify_data(m)
        query_class = IncidentPilot._classify_user_query(user_input)

        logger.debug("Contradiction check: data=%s query=%s", data_class, query_class)

        if data_class == "normal" or query_class is None:
            return None

        return IncidentPilot._build_contradiction_text(data_class, query_class)

    @staticmethod
    def _missing_rag_citation(response_text: str, chunks: list[dict]) -> bool:
        """True if RAG returned chunks this turn but the response cites none
        of them.

        Mirrors the system prompt's citation contract (prompts/system_prompt.md,
        "Citations"): a [Runbook: ...] or [Postmortem: ...] tag is required
        whenever RAG returned relevant context. Prompting alone doesn't
        reliably produce this, especially on smaller models, which tend to
        answer entirely from tool results and drop the RAG context -- this
        is the code-level backstop.
        """
        if not chunks:
            return False
        return "[Runbook" not in response_text and "[Postmortem" not in response_text

    # ------------------------------------------------------------------
    # Trace data (exposed for the Gradio UI trace panel)
    # ------------------------------------------------------------------

    def get_trace(self) -> dict:
        """Return the trace data from the last query for the UI trace panel.

        Returns a dict with keys:
          - ``queries``: original query + HyDE-expanded queries actually run
            against the vector store this turn
          - ``chunks``: list of RAG chunk dicts ``{source, section, content}``
          - ``metrics``: live metrics snapshot ``{name, service, endpoint, value}`` list
            (empty if the model never called ``query_metrics`` this turn)
          - ``log_analysis``: structured log analysis dict (empty if
            ``query_logs`` was never called this turn)
          - ``trace_summary``: distributed-trace summary dict (same)
          - ``trace_id``: sample failed trace's ID, if one was reconstructed
          - ``tool_calls``: list of ``{name, args, result}`` for every MCP
            tool call the model made this turn (empty list if none)
          - ``augmented_input``: the initial prompt sent to the LLM
          - ``source``: "live" | "unavailable" | "not_queried"
            ("not_queried" means the model answered without calling either
            tool; "unavailable" means a tool was called but Prometheus/Loki
            could not be reached)
          - ``timings``: list of ``{phase, duration_ms}`` for every phase run
            this turn (hyde_expansion, rag_retrieval, initial_llm_call,
            tool_round_N_execution/_llm_call, contradiction_check,
            citation_check, ..., ending with a ``total`` entry) -- lets the
            UI show where time actually went.
        """
        return (self._last_trace or {}).copy()

    @staticmethod
    def _merge_source(metrics_result: dict | None, logs_result: dict | None) -> str:
        """Combine the ``source`` fields of whichever tool(s) were actually
        called this turn into one label for the UI badge."""
        if metrics_result is None and logs_result is None:
            return "not_queried"
        sources = [r.get("source") for r in (metrics_result, logs_result) if r is not None]
        return "live" if "live" in sources else "unavailable"

    # ------------------------------------------------------------------
    # Main query
    # ------------------------------------------------------------------

    @track(name="incident_triage")
    def query(self, user_input: str, service: str | None = None,
              history: list[tuple[str, str]] | None = None) -> str:
        """Run a full triage query: RAG retrieval (always) + an MCP
        tool-calling loop over ``query_metrics``/``query_logs`` that the
        model itself decides whether/when to use + a cited answer.

        Args:
            user_input: The engineer's incident description.
            service: Optional hint passed to the model on which service to
                     scope any telemetry query to, if it decides to call one.
            history: Optional sliding window of prior ``(user, assistant)``
                     raw-text turns to give the model conversational memory.
                     Already windowed by the caller (the UI/session layer);
                     the pilot stays stateless and just injects it. Raw text
                     only -- never the RAG-augmented form or tool rounds --
                     so a few turns of memory don't blow the model's token
                     ceiling.

        Returns the LLM's cited triage summary as a string.
        """
        req_id = get_request_id() or "-"
        logger.info("Processing query [req=%s]: '%s...'", req_id, user_input[:80])

        # Correlate this Phoenix trace with the app's [req=<id>] logs. No-op when
        # tracing is disabled or there's no active trace.
        update_trace_metadata(metadata={
            "request_id": req_id,
            "model": self._model_name,
            "service": service,
        })

        query_start = time.perf_counter()
        timings: list[dict] = []

        def _mark(label: str, phase_start: float) -> None:
            elapsed_ms = round((time.perf_counter() - phase_start) * 1000, 1)
            timings.append({"phase": label, "duration_ms": elapsed_ms})
            logger.info("[req=%s] %s complete (%.1fms)", req_id, label, elapsed_ms)

        # 1. RAG retrieval -- always runs. HyDE (_expand_query) turns the
        #    engineer's raw description into targeted runbook-vocabulary
        #    searches before hitting the vector store.
        logger.info("[req=%s] HyDE expansion starting", req_id)
        t0 = time.perf_counter()
        queries = self._expand_query(user_input)
        _mark("hyde_expansion", t0)

        logger.info("[req=%s] RAG retrieval starting", req_id)
        t0 = time.perf_counter()
        chunks = self._retrieve_with_queries(queries)
        _mark("rag_retrieval", t0)
        rag_block = self._format_context(chunks)

        scope_hint = (
            f'\n\n(If you query telemetry, scope it to service="{service}".)'
            if service else ""
        )
        augmented_input = (
            f"## Retrieved context (RAG)\n\n{rag_block}\n\n"
            f"---\n\n"
            f"Engineer's incident description:\n{user_input}{scope_hint}"
        )
        # Conversational memory: splice prior turns between the system prompt
        # and the current augmented turn so a follow-up ("and the logs?")
        # carries context. Raw text only -- the current turn is the only one
        # that gets the fat RAG block.
        messages: list = [SystemMessage(content=self.system_prompt)]
        for prev_user, prev_answer in (history or []):
            messages.append(HumanMessage(content=prev_user))
            messages.append(AIMessage(content=prev_answer))
        messages.append(HumanMessage(content=augmented_input))

        tool_trace: list[dict] = []
        last_metrics_result: dict | None = None
        last_logs_result: dict | None = None

        # 2. Priority-1 guardrail backstop: for messages that look like a
        #    deploy/rollback/hotfix/config-change request, don't even bind
        #    tools to this call. The system prompt already tells the model
        #    not to call a tool for these -- this makes it impossible for a
        #    tool call to happen at all, rather than merely discouraged.
        action_request = _looks_like_action_request(user_input)
        model_for_this_turn = self.model if action_request else self.model_with_tools
        if action_request:
            logger.info("[req=%s] action-request pattern matched -- tools not bound this turn", req_id)

        # 3. Let the model decide whether/which tools to call (when bound),
        #    executing real MCP round trips for whatever it asks for.
        logger.info("[req=%s] initial LLM call starting (tools_bound=%s)", req_id, not action_request)
        t0 = time.perf_counter()
        response = model_for_this_turn.invoke(messages)
        _mark("initial_llm_call", t0)

        requested_calls = [
            f"{c['name']}({c.get('args', {})})"
            for c in (getattr(response, "tool_calls", None) or [])
        ]
        logger.info("[req=%s] tool calls required: %s", req_id, requested_calls or "none")

        rounds = 0
        while getattr(response, "tool_calls", None) and rounds < MAX_TOOL_ROUNDS:
            messages.append(response)
            t0 = time.perf_counter()
            for call in response.tool_calls:
                result = self._call_mcp_tool(call["name"], call["args"])
                tool_trace.append({"name": call["name"], "args": call["args"], "result": result})
                if call["name"] == "query_metrics":
                    last_metrics_result = result
                elif call["name"] == "query_logs":
                    last_logs_result = result
                messages.append(ToolMessage(content=json.dumps(result), tool_call_id=call["id"]))
            _mark(f"tool_round_{rounds + 1}_execution", t0)
            rounds += 1
            t0 = time.perf_counter()
            response = model_for_this_turn.invoke(messages)
            _mark(f"tool_round_{rounds}_llm_call", t0)

        if getattr(response, "tool_calls", None):
            # Hit MAX_TOOL_ROUNDS while the model still wanted to call
            # tools -- force a final textual answer from what's gathered.
            logger.warning("[req=%s] hit MAX_TOOL_ROUNDS=%d with tool calls still pending",
                            req_id, MAX_TOOL_ROUNDS)
            messages.append(response)
            messages.append(HumanMessage(
                content="Give your final answer now, using only the tool "
                        "results already gathered above."
            ))
            t0 = time.perf_counter()
            response = self.model.invoke(messages)
            _mark("forced_final_llm_call", t0)

        # 4. Contradiction check -- only meaningful if query_metrics was
        #    actually called; fold the flag back in if one was found.
        t0 = time.perf_counter()
        contradiction_warning = self._detect_contradictions(user_input, last_metrics_result or {})
        _mark("contradiction_check", t0)
        if contradiction_warning:
            logger.info("[req=%s] contradiction detected: %s", req_id, contradiction_warning)
            messages.append(response)
            messages.append(HumanMessage(
                content=f"## Contradiction check\n\n{contradiction_warning}\n\n"
                        f"Revise your answer to explicitly flag this, per your "
                        f"[Contradiction] citation rule."
            ))
            t0 = time.perf_counter()
            response = model_for_this_turn.invoke(messages)
            _mark("contradiction_revision_llm_call", t0)

        # 5. Citation enforcement -- prompting alone isn't reliable, especially
        #    on smaller models: they'll often answer entirely from tool
        #    results and drop the retrieved RAG context. Skip for action-
        #    request turns (guardrail refusals aren't triage answers and
        #    don't need a [Runbook]/[Postmortem] tag).
        t0 = time.perf_counter()
        missing_citation = not action_request and self._missing_rag_citation(response.content, chunks)
        _mark("citation_check", t0)
        if missing_citation:
            logger.info("[req=%s] RAG chunks retrieved but no [Runbook]/[Postmortem] tag in "
                        "response -- requesting revision", req_id)
            messages.append(response)
            messages.append(HumanMessage(
                content="## Citation check\n\n"
                        "Your answer didn't cite any of the retrieved runbook/postmortem "
                        "context with a [Runbook: ...] or [Postmortem: ...] tag, even though "
                        "relevant chunks were retrieved this turn. Revise your answer to "
                        "explicitly cite the retrieved context you actually used, per the "
                        "Citations rule -- don't just describe it in prose."
            ))
            t0 = time.perf_counter()
            response = model_for_this_turn.invoke(messages)
            _mark("citation_revision_llm_call", t0)

        total_ms = round((time.perf_counter() - query_start) * 1000, 1)
        timings.append({"phase": "total", "duration_ms": total_ms})
        logger.info("[req=%s] LLM response done -- %d characters, %d tool call(s), "
                     "total %.1fms across %d phase(s)",
                     req_id, len(response.content), len(tool_trace), total_ms, len(timings) - 1)

        # --- Build trace for the UI ---
        # query_metrics already returns condensed {name, service, endpoint,
        # value} entries (see mcp_server.server._condense_metrics) -- no
        # further reshaping needed here.
        trace_metrics = (last_metrics_result or {}).get("metrics", [])[:12]

        trace_log_analysis = (last_logs_result or {}).get("log_analysis", {})
        trace_summary = (last_logs_result or {}).get("trace_summary", {})

        self._last_trace = {
            "queries": queries,
            "chunks": chunks,
            "metrics": trace_metrics,
            "log_analysis": trace_log_analysis,
            "trace_summary": trace_summary,
            "trace_id": trace_summary.get("sample_trace_id"),
            "tool_calls": tool_trace,
            "augmented_input": augmented_input,
            "source": self._merge_source(last_metrics_result, last_logs_result),
            "contradiction": contradiction_warning,
            "timings": timings,
            "request_id": get_request_id(),
        }

        return response.content


def _separator(label: str) -> None:
    width = 72
    print("\n" + "=" * width)
    print(f"  TEST: {label}")
    print("=" * width)


if __name__ == "__main__":
    setup_logging()
    configure_observability()

    pilot = IncidentPilot()

    _separator("triage (RAG + agent-decided MCP tool calls)")
    print(f"USER:\n  {TRIAGE_QUERY}\n")

    response = pilot.query(TRIAGE_QUERY)
    trace = pilot.get_trace()
    print(f"[HyDE queries: {trace.get('queries', [])}]")
    print(f"[retrieved {len(trace.get('chunks', []))} chunk(s): "
          f"{[c['source'] + ' / ' + c['section'] for c in trace.get('chunks', [])]}]")
    print(f"[tool calls made: {[t['name'] for t in trace.get('tool_calls', [])]}]")
    print(f"[data source: {trace.get('source')}]\n")

    print(f"INCIDENT PILOT:\n{response}")
    pilot.close()

    print("\n" + "=" * 72)
    print("  Triage query completed. Verify the response cites the runbook")
    print("  source/section returned above rather than inventing steps.")
    print("=" * 72 + "\n")
