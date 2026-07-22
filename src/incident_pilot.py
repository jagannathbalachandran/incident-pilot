"""
IncidentPilot agent — Week 1, Task 3 + Task 8.

Initialises ChatGroq with the triage-copilot system prompt, retrieves
grounding chunks from the RAG vector store, queries live Prometheus/
Loki metrics (with static fallback), and returns a cited triage summary.

Running this file directly fires a grounded triage query against the
connection-pool-exhaustion runbook.

Requires:
    GROQ_API_KEY environment variable (or a .env file at the repo root).
    A vector store built via ``python src/ingestion.py``.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

from logging_config import setup_logging
from query_logs import analyze_logs as _analyze_logs
from query_logs import analyze_traces as _analyze_traces
from query_logs import query_logs as _query_logs
from request_context import get_request_id

logger = logging.getLogger(__name__)

# Load .env from repo root so GROQ_API_KEY is available without a shell export
load_dotenv(Path(__file__).parent.parent / ".env")

SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system_prompt.md"
VECTORSTORE_DIR = Path(__file__).parent.parent / "synthetic-data" / "vectorstore"

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

    def __init__(self):
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            logger.error("GROQ_API_KEY is not set")
            raise EnvironmentError(
                "GROQ_API_KEY is not set. "
                "Export it in your shell or add it to a .env file at the repo root."
            )

        logger.info("Initialising IncidentPilot with model=llama-3.3-70b-versatile")
        self.system_prompt = SYSTEM_PROMPT_PATH.read_text()
        self.model = ChatGroq(
            model="llama-3.3-70b-versatile",
            api_key=api_key,
        )
        self.vectorstore = self._load_vectorstore()

        # Cache for trace data (exposed via get_trace() for the UI)
        self._last_logs_query: dict | None = None
        self._last_trace: dict | None = None

        logger.info("IncidentPilot initialised (vectorstore=%s)",
                     "loaded" if self.vectorstore else "unavailable")

    # ------------------------------------------------------------------
    # RAG retrieval
    # ------------------------------------------------------------------

    def _load_vectorstore(self) -> Chroma | None:
        if not VECTORSTORE_DIR.exists():
            logger.warning("Vector store directory not found at %s — RAG disabled",
                           VECTORSTORE_DIR)
            return None
        logger.debug("Loading embedding model (all-MiniLM-L6-v2)...")
        embeddings = HuggingFaceEmbeddings(
            model_name="all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
        )
        logger.debug("Opening ChromaDB at %s", VECTORSTORE_DIR)
        return Chroma(
            persist_directory=str(VECTORSTORE_DIR),
            embedding_function=embeddings,
        )

    def retrieve(self, user_input: str, k: int = 3) -> list[dict]:
        """Return top-k grounding chunks as ``{source, section, content}`` dicts."""
        if self.vectorstore is None:
            logger.warning("retrieve() called but vectorstore is unavailable")
            return []
        logger.debug("RAG similarity_search(k=%d): query='%s...'", k, user_input[:60])
        results = self.vectorstore.similarity_search(user_input, k=k)
        chunks = [
            {
                "source": doc.metadata.get("source", "unknown"),
                "section": doc.metadata.get("section", "unknown"),
                "content": doc.page_content,
            }
            for doc in results
        ]
        logger.info("RAG retrieved %d chunk(s): %s", len(chunks),
                     [f"{c['source']} / {c['section']}" for c in chunks])
        return chunks

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
    # Live metrics / logs query
    # ------------------------------------------------------------------

    def query_logs(self, timeframe: str = "15m", service: str | None = None) -> dict:
        """Query live metrics and logs, returning a dict with keys
        ``metrics``, ``logs``, and ``source``.

        ``service=None`` (the default) queries across every simulated
        service, which is what's needed to reconstruct a user journey
        whose spans land in more than one service's log stream. Pass a
        service name to scope the query to just that service.

        The result is cached in ``self._last_logs_query`` for the UI.
        """
        logger.debug("query_logs(timeframe='%s', service='%s')", timeframe, service or "all")
        self._last_logs_query = _query_logs(service=service, timeframe=timeframe)
        source = self._last_logs_query.get("source", "unknown")
        metrics_count = len(self._last_logs_query.get("metrics") or [])
        logs_count = len(self._last_logs_query.get("logs") or [])
        logger.info("query_logs result: source=%s metrics=%d logs=%d",
                     source, metrics_count, logs_count)
        return self._last_logs_query

    def _format_live_data(self, logs_result: dict) -> str:
        """Format the live metrics/logs result into a text block for the LLM.

        Metrics are shown as a snapshot. Logs are analyzed for patterns
        (level breakdown, top messages, error clusters) rather than dumped
        as raw lines, so the LLM gets a structured summary.
        """
        source_label = logs_result.get("source", "unavailable")
        parts = [f"[Data source: {source_label}]"]
        logger.debug("Formatting live data (source=%s)", source_label)

        # --- Metrics ---
        metrics = logs_result.get("metrics")
        if metrics:
            lines = []
            for series in metrics[:12]:
                m = series.get("metric", {})
                name = m.get("__name__", "unknown")
                svc = m.get("service", "")
                endpoint = m.get("endpoint", "")
                scope = f"service={svc}" + (f",endpoint={endpoint}" if endpoint else "")
                values = series.get("values", [])
                if values:
                    latest = values[-1][1]
                    lines.append(f"  {name}{{{scope}}}: {latest} (latest)")
            if lines:
                parts.append("Live metrics (sampled):\n" + "\n".join(lines))

        # --- Distributed traces ---
        logs = logs_result.get("logs")
        if logs:
            traces = _analyze_traces(logs)
            if traces["total_traces"]:
                trace_blocks = [
                    f"  Journeys observed: {traces['total_traces']} "
                    f"({traces['failed_traces']} failed, {traces['affected_users']} user(s) affected)"
                ]
                if traces["break_points"]:
                    trace_blocks.append("  Most common break points:")
                    for bp in traces["break_points"]:
                        trace_blocks.append(
                            f"    {bp['service']}{bp['endpoint']} -> {bp['status_code']} ({bp['count']}x)"
                        )
                if traces["sample_path"]:
                    path_str = " -> ".join(
                        f"{s['service']}{s['endpoint']}({s['status_code']})" for s in traces["sample_path"]
                    )
                    trace_blocks.append(
                        f"  Sample failed journey (trace_id={traces['sample_trace_id']}): {path_str}"
                    )
                parts.append("Distributed traces (login -> ... -> logout):\n" + "\n".join(trace_blocks))

        # --- Log analysis ---
        if logs:
            analysis = _analyze_logs(logs)
            logger.debug("Log analysis: %d entries, error_rate=%.1f%%",
                         analysis["total_entries"], analysis["error_rate_pct"])
            log_blocks = []

            # Level breakdown
            if analysis["by_level"]:
                level_str = ", ".join(
                    f"{k}: {v}" for k, v in sorted(analysis["by_level"].items())
                )
                log_blocks.append(f"  Log level breakdown: {level_str}")

            if analysis["error_rate_pct"] > 0:
                log_blocks.append(
                    f"  Error rate: {analysis['error_rate_pct']}% of log entries"
                )

            # Top messages
            if analysis["top_messages"]:
                log_blocks.append("  Most frequent log patterns:")
                for msg in analysis["top_messages"][:5]:
                    log_blocks.append(
                        f"    [{msg['level']}] \"{msg['pattern']}\" — {msg['count']}x"
                    )

            # Error clusters
            if analysis["error_clusters"]:
                log_blocks.append(
                    f"  Error clusters detected: {analysis['error_cluster_count']}"
                )
                for cluster in analysis["error_clusters"][:3]:
                    log_blocks.append(
                        f"    {cluster['count']} errors around {cluster['start']}"
                    )

            if log_blocks:
                parts.append("Log analysis:\n" + "\n".join(log_blocks))

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Code-level contradiction detection
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_live_metrics(metrics: list, service: str = "checkout-api") -> dict:
        """Extract named metric values for one service from the raw
        Prometheus series list.

        Returns a dict like ``{"svc_p99_latency_ms": 1486, "svc_error_rate_pct": 4.8, ...}``.
        Only the **last** value from each matching series is kept. The
        contradiction-check machinery below is scoped to one service
        (``checkout-api`` by default, matching where the pool/cache/fraud
        scenarios default to) even though live queries now span every
        simulated service.
        """
        parsed: dict[str, float] = {}
        if not metrics:
            return parsed
        for series in metrics:
            m = series.get("metric", {})
            name = m.get("__name__", "")
            if service and m.get("service") != service:
                continue
            values = series.get("values", [])
            if name and values:
                try:
                    parsed[name] = float(values[-1][1])
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

    # ------------------------------------------------------------------
    # Trace data (exposed for the Gradio UI trace panel)
    # ------------------------------------------------------------------

    def get_trace(self) -> dict:
        """Return the trace data from the last query for the UI trace panel.

        Returns a dict with keys:
          - ``chunks``: list of RAG chunk dicts ``{source, section, content}``
          - ``metrics``: live metrics snapshot ``{name, service, endpoint, value}`` list
          - ``log_analysis``: structured log analysis dict (from ``analyze_logs``)
          - ``trace_summary``: distributed-trace summary dict (from ``analyze_traces``)
          - ``trace_id``: sample failed trace's ID, if one was reconstructed
          - ``augmented_input``: the full prompt sent to the LLM
          - ``source``: data source label ("live" | "static_fallback" | "unavailable")
        """
        return (self._last_trace or {}).copy()

    # ------------------------------------------------------------------
    # Main query
    # ------------------------------------------------------------------

    def query(
        self,
        user_input: str,
        live_data_timeframe: str = "15m",
        logs_result: dict | None = None,
        service: str | None = None,
    ) -> str:
        """Run a full triage query: RAG retrieval + live metrics + LLM.

        Args:
            user_input: The engineer's incident description.
            live_data_timeframe: Time window for live data (default 15m).
            logs_result: Pre-queried logs result to avoid querying twice.
                         If None, queries live data automatically.
            service: Scope the live query to one service; None queries
                     across all of them (needed for journey reconstruction).

        Returns the LLM's cited triage summary as a string.
        """
        req_id = get_request_id() or "-"
        logger.info("Processing query [req=%s]: '%s...' (timeframe=%s, service=%s)",
                     req_id, user_input[:80], live_data_timeframe, service or "all")

        # 1. RAG retrieval
        chunks = self.retrieve(user_input)
        rag_block = self._format_context(chunks)

        # 2. Live metrics / logs (use pre-queried result if provided)
        if logs_result is None:
            logs_result = self.query_logs(timeframe=live_data_timeframe, service=service)
        live_block = self._format_live_data(logs_result)

        # 3. Detect contradictions between user query and live data
        contradiction_warning = self._detect_contradictions(user_input, logs_result)
        if contradiction_warning:
            logger.info("Contradiction detected: %s", contradiction_warning)

        # 4. Build the augmented prompt (with contradiction warning if any)
        contradiction_block = (
            f"\n\n## Contradiction check\n\n"
            f"{contradiction_warning}"
            if contradiction_warning else ""
        )
        augmented_input = (
            f"## Retrieved context (RAG)\n\n"
            f"{rag_block}\n\n"
            f"## Live metrics & logs\n\n"
            f"{live_block}\n\n"
            f"---\n\n"
            f"Engineer's incident description:\n{user_input}"
            f"{contradiction_block}"
        )

        req_id = get_request_id() or "-"
        logger.debug("Calling LLM [req=%s] (model=llama-3.3-70b-versatile)", req_id)
        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=augmented_input),
        ]

        response = self.model.invoke(messages)
        logger.info("LLM response received [req=%s] (%d characters)",
                     req_id, len(response.content))

        # --- Build trace for the UI ---
        trace_metrics = []
        metrics_raw = (logs_result or {}).get("metrics", [])
        if metrics_raw:
            for series in metrics_raw[:12]:
                m = series.get("metric", {})
                name = m.get("__name__", "unknown")
                values = series.get("values", [])
                if values:
                    trace_metrics.append({
                        "name": name, "service": m.get("service", ""),
                        "endpoint": m.get("endpoint", ""), "value": values[-1][1],
                    })

        trace_logs = logs_result or {}
        trace_log_analysis = {}
        trace_summary = {}
        log_entries = trace_logs.get("logs")
        if log_entries:
            trace_log_analysis = _analyze_logs(log_entries)
            trace_summary = _analyze_traces(log_entries)

        self._last_trace = {
            "chunks": chunks,
            "metrics": trace_metrics,
            "log_analysis": trace_log_analysis,
            "trace_summary": trace_summary,
            "trace_id": trace_summary.get("sample_trace_id"),
            "augmented_input": augmented_input,
            "source": (logs_result or {}).get("source", "unavailable"),
            "contradiction": contradiction_warning,
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

    pilot = IncidentPilot()

    _separator("triage (RAG + live data)")
    print(f"USER:\n  {TRIAGE_QUERY}\n")

    retrieved = pilot.retrieve(TRIAGE_QUERY)
    print(f"[retrieved {len(retrieved)} chunk(s): "
          f"{[c['source'] + ' / ' + c['section'] for c in retrieved]}]\n")

    # Test live query (will fall back to static if Prometheus is unreachable)
    live = pilot.query_logs(timeframe="15m")
    print(f"[live data source: {live.get('source')}]")
    print(f"[metrics series: {len(live.get('metrics') or [])}]")
    print(f"[log entries: {len(live.get('logs') or [])}]\n")

    response = pilot.query(TRIAGE_QUERY)
    print(f"INCIDENT PILOT:\n{response}")

    print("\n" + "=" * 72)
    print("  Triage query completed. Verify the response cites the runbook")
    print("  source/section returned above rather than inventing steps.")
    print("=" * 72 + "\n")
