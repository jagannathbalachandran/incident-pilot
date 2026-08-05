"""
IncidentPilot Gradio UI — Week 1, Task 9.

An engineer types an incident description and gets a RAG-grounded, cited triage
summary back from IncidentPilot.query() — enriched with live Prometheus/Loki
metrics (across every simulated service) when the monitoring stack is running.

Includes an expandable **trace panel** showing the agent's reasoning: which RAG
chunks were retrieved, what live data (including any reconstructed user
journey) was returned, and the full prompt sent to the LLM.

Tab 2: **Incident Control** lets you trigger/resolve scenarios directly from the
Gradio UI without using curl — pool exhaustion or cache failover, optionally
targeting a specific service.

Usage:
    python src/app.py
"""

import json
import logging
import os

import gradio as gr
import requests

# --- Workaround for a gradio_client 1.3.0 (bundled with gradio 4.44) bug ---
# Building the API schema for a component whose JSON schema contains a boolean
# value (e.g. `additionalProperties: true`, which gr.Chatbot(type="messages")
# produces) crashes with `TypeError: argument of type 'bool' is not iterable`
# in get_type() -- which aborts demo.launch(). Patch the one function that
# passes a bool schema through so it degrades to "bool" instead of crashing.
# No-op on versions where the bug is already fixed.
import gradio_client.utils as _gc_utils

_gc_orig_j2p = _gc_utils._json_schema_to_python_type


def _gc_json_schema_to_python_type(schema, defs=None):
    if isinstance(schema, bool):
        return "bool"
    return _gc_orig_j2p(schema, defs)


_gc_utils._json_schema_to_python_type = _gc_json_schema_to_python_type
# --- end workaround ---

import chat_store
from incident_pilot import IncidentPilot, ToolCallServiceError
from logging_config import setup_logging
from observability import configure_observability
from request_context import set_request_id

logger = logging.getLogger(__name__)

pilot = IncidentPilot()

# Sliding-window size for conversational memory: how many prior (user,
# assistant) turn pairs to replay into the model. Kept small because each
# turn's HumanMessage already carries a fat RAG block, and llama-3.1-8b-instant
# has a 6000 tokens-per-minute per-request ceiling -- a few turns is plenty
# without risking a 413/429.
HISTORY_WINDOW_TURNS = 3

# Incident-generator API base URL (Docker host port — override via env var)
_FLASK_API = os.getenv("FLASK_API_URL", "http://localhost:5001")

# The four services the UI exposes. The generator's topology still declares a
# couple of internal-only dependencies (inventory-svc, fraud-scoring-svc), but
# those are hidden here -- both dropdowns and the capability hint are filtered
# to this allowlist so they never surface even when /api/services returns them.
_ALLOWED_SERVICES = {
    "auth-service",
    "listing-service",
    "checkout-api",
    "payment-service",
}

# Static fallback if the generator isn't reachable yet when the UI starts.
# Mirrors flask-generator/topology.py: all four services now declare both a db
# pool and a cache, so each supports both pool-exhaustion and cache-failover.
_FALLBACK_SERVICE_INFO = [
    {"name": "auth-service", "uses_db_pool": True, "uses_cache": True},
    {"name": "listing-service", "uses_db_pool": True, "uses_cache": True},
    {"name": "checkout-api", "uses_db_pool": True, "uses_cache": True},
    {"name": "payment-service", "uses_db_pool": True, "uses_cache": True},
]


def _fetch_service_info() -> list:
    """Fetch each allowed service's capability info (name, uses_db_pool,
    uses_cache) from the generator. Falls back to a static list if unreachable.

    Filtered to ``_ALLOWED_SERVICES`` so the generator's internal-only
    dependencies (inventory-svc, fraud-scoring-svc) never appear in the UI even
    though /api/services still returns them.
    """
    try:
        resp = requests.get(f"{_FLASK_API}/api/services", timeout=5)
        data = resp.json()
        info = [
            {
                "name": s["name"],
                "uses_db_pool": s.get("uses_db_pool", False),
                "uses_cache": s.get("uses_cache", False),
            }
            for s in data.get("services", [])
            if s.get("name") in _ALLOWED_SERVICES
        ]
        if info:
            return info
    except Exception as exc:
        logger.warning("Failed to fetch service list, using static fallback: %s", exc)
    return list(_FALLBACK_SERVICE_INFO)


_SERVICE_INFO = _fetch_service_info()
SERVICE_CAPABILITIES = {s["name"]: s for s in _SERVICE_INFO}
SERVICE_NAMES = [s["name"] for s in _SERVICE_INFO]
SERVICE_CHOICES = ["(kind default)"] + SERVICE_NAMES
TRIAGE_SERVICE_CHOICES = ["(all services)"] + SERVICE_NAMES


def _service_capability_hint(service: str) -> str:
    """Short markdown line showing which incident kinds a service supports,
    so picking an invalid (kind, service) combo is visible before you click."""
    if not service or service == "(kind default)":
        return "_Pick a service to see which incident kinds it supports (or leave this as the kind's own default target)._"
    caps = SERVICE_CAPABILITIES.get(service)
    if caps is None:
        return f"_No capability info available for `{service}`._"
    pool_mark = "✅" if caps["uses_db_pool"] else "❌"
    cache_mark = "✅" if caps["uses_cache"] else "❌"
    return (
        f"**`{service}` supports:** {pool_mark} Pool Exhaustion&nbsp;&nbsp;|&nbsp;&nbsp;"
        f"{cache_mark} Cache Failover"
    )


# ---------------------------------------------------------------------------
# Incident Control helpers (Tab 2 — trigger/resolve via the generator API)
# ---------------------------------------------------------------------------


def _get_next_steps(active: list) -> str:
    """Return short, actionable next-step guidance for the active incident(s)."""
    if not active:
        return (
            "### 📋 Next Steps\n\n"
            "No incident active. You can:\n\n"
            "1. **🎮 Pick a service** below (or leave the kind's default) and click a trigger button\n"
            "2. **📊 Open Grafana** at http://localhost:3000 (admin/admin) → Dashboards\n"
            "3. **🔍 Try AI Triage** — switch to the 🚑 Triage tab and describe a symptom"
        )

    blocks = []
    for inc in active:
        kind, service, phase = inc.get("kind"), inc.get("service"), inc.get("phase")
        rid = inc.get("request_id", "-")
        blocks.append(
            f"### 📋 `{kind}` on `{service}` — phase: `{phase}`\n\n"
            f"**1. 🖥️ Open Grafana** → http://localhost:3000/dashboards → look for the "
            f"`{service}` dashboard.\n\n"
            f"**2. 🔍 Check Loki logs for this incident's traffic:**\n"
            "   → Open Grafana **Explore**, select **Loki**, run:\n"
            "   ```logql\n"
            f'   {{service="{service}"}} | json | request_id="{rid}"\n'
            "   ```\n"
            f"   (`{rid}` is the request ID of the API call that triggered this incident — "
            "every span downstream of it during the incident carries its own request_id, "
            "but you can browse `{service=\"" + service + "\"}` broadly to see the shape.)"
        )
    return "\n\n---\n\n".join(blocks)


def _get_state_markdown() -> str:
    """Fetch current incident state from the generator and format as markdown."""
    try:
        resp = requests.get(f"{_FLASK_API}/api/incidents/state", timeout=5)
        data = resp.json()
    except Exception as exc:
        logger.warning("Failed to fetch incident state: %s", exc)
        return f"❌ **Connection error:** `{exc}`\n\nMake sure `docker compose up -d` is running."

    active = data.get("active", [])
    count = data.get("count", len(active))

    if not active:
        state_table = f"### 📊 Current Incident State\n\n*No active incidents.* (count={count})\n"
    else:
        rows = "\n".join(
            f"| `{i['kind']}` | `{i['service']}` | `{i['phase']}` | `{i['tick_count']}` | "
            f"`{i['auto_resolve']}` | `{i.get('request_id', '-')}` |"
            for i in active
        )
        state_table = (
            f"### 📊 Current Incident State ({count} active)\n\n"
            "| Kind | Service | Phase | Tick | Auto Resolve | Request ID |\n"
            "|---|---|---|---|---|---|\n"
            f"{rows}\n"
        )

    next_steps = _get_next_steps(active)
    if next_steps:
        state_table += "\n---\n\n" + next_steps

    return state_table


def _resolve_service_arg(service: str) -> str | None:
    """Map the dropdown's placeholder value to None (no filter)."""
    return None if not service or service == "(kind default)" else service


def _trigger(kind: str, service: str = "", auto_resolve: bool = True):
    """Trigger an incident scenario via the generator API, optionally targeting ``service``."""
    target = _resolve_service_arg(service)
    logger.info("Triggering %s (service=%s, auto_resolve=%s)", kind, target or "default", auto_resolve)
    try:
        resp = requests.post(
            f"{_FLASK_API}/api/incidents/{kind}/trigger",
            json={"auto_resolve": auto_resolve, "service": target},
            timeout=5,
        )
        data = resp.json()
    except Exception as exc:
        logger.warning("Trigger failed: %s", exc)
        msg = f"❌ **Trigger error:** `{exc}`"
        return msg, _get_state_markdown()

    if resp.status_code >= 400:
        msg = f"❌ **Trigger rejected:** `{data.get('error', 'unknown error')}`"
        return msg, _get_state_markdown()

    status = data.get("status", "error")
    rid = data.get("request_id", "-")
    phase = data.get("phase", "?")
    svc = data.get("service", target or "?")

    msg = (
        f"✅ **Incident triggered!**\n\n"
        f"| Field | Value |\n"
        f"|---|---|\n"
        f"| **Kind** | `{kind}` |\n"
        f"| **Service** | `{svc}` |\n"
        f"| **Status** | `{status}` |\n"
        f"| **Phase** | `{phase}` |\n"
        f"| **Request ID** | `{rid}` |\n"
    )
    return msg, _get_state_markdown()


def _resolve_current(service: str = ""):
    """Resolve active incident(s) — scoped to ``service`` if one is selected, else all."""
    target = _resolve_service_arg(service)
    logger.info("Resolving current incident(s) (service=%s)", target or "all")
    try:
        params = {"service": target} if target else {}
        resp = requests.post(f"{_FLASK_API}/api/incidents/current/resolve", params=params, timeout=5)
        data = resp.json()
    except Exception as exc:
        logger.warning("Resolve failed: %s", exc)
        msg = f"❌ **Resolve error:** `{exc}`"
        return msg, _get_state_markdown()

    status = data.get("status", "?")
    resolved = data.get("resolved", [])

    if status == "resolved":
        summary = ", ".join(f"`{r['kind']}@{r['service']}`" for r in resolved)
        msg = f"🛑 **Resolved:** {summary} — state should return to baseline."
    elif status == "no_active_incident":
        msg = "ℹ️ **No matching active incident** to resolve."
    else:
        msg = f"❓ Resolve status: `{status}`"

    return msg, _get_state_markdown()


def _refresh_state():
    """Refresh the state display."""
    return _get_state_markdown()


EXAMPLE_QUERIES = [
    "Please Roll back the last deploy.",
    "Just push a hotfix directly to production now.",
    "checkout-api seems to be slow, what happened?",
    "API latency spiked 5x in the last 15 minutes, what's going on?",
    "What does the runbook say to do for a connection-pool exhaustion?",
    # Commented out for demo -- keep in code, just not shown in the UI.
    # "checkout-api p99 latency has been climbing gradually over the last 15 minutes, no sudden step change. What's going on and what does the runbook say to do?",
    # "A user says they logged in, browsed listings, but checkout failed — can you trace what happened to their session?",
    # "payment-service looks slow — is that affecting checkout-api too?",
]


def _format_trace(trace: dict) -> str:
    """Build a Markdown trace panel from the pilot's trace data."""
    parts = []

    # --- Contradiction warning (highest visibility) ---
    contradiction = trace.get("contradiction")
    if contradiction:
        parts.append(
            "### 🚨 Contradiction Detected\n\n"
            "> " + contradiction + "\n"
        )

    # --- Request ID ---
    request_id = trace.get("request_id", "")
    if request_id:
        parts.append(f"**Request ID (this query):** `{request_id}`")

    # --- Data Source ---
    source = trace.get("source", "unavailable")
    if source == "live":
        source_badge = "🟢 **Live (Prometheus + Loki)**"
    else:
        source_badge = "🔴 **Unavailable — unable to reach Prometheus/Loki**"
    parts.append(f"**Data source:** {source_badge}")

    # --- Timing breakdown (where the time actually went) ---
    timings = trace.get("timings", [])
    if timings:
        phase_lines = [
            f"  `{t['phase']}`: {t['duration_ms']}ms"
            for t in timings if t["phase"] != "total"
        ]
        total = next((t["duration_ms"] for t in timings if t["phase"] == "total"), None)
        if total is not None:
            phase_lines.append(f"  **total:** {total}ms")
        parts.append("**Timing breakdown:**\n" + "\n".join(phase_lines))

    # --- Tool calls (agent-decided) ---
    tool_calls = trace.get("tool_calls", [])
    if tool_calls:
        call_lines = [
            f"  {i}. `{c['name']}({', '.join(f'{k}={v!r}' for k, v in c['args'].items())})` "
            f"→ source=`{c['result'].get('source', '?')}`"
            for i, c in enumerate(tool_calls, 1)
        ]
        parts.append("**Tool calls made this turn:**\n" + "\n".join(call_lines))
    else:
        parts.append("**Tool calls made this turn:** *None — the agent judged live telemetry wasn't needed*")

    # --- RAG Chunks ---
    chunks = trace.get("chunks", [])
    if chunks:
        chunk_lines = []
        for i, c in enumerate(chunks, 1):
            snippet = c.get("content", "")[:150].replace("\n", " ")
            chunk_lines.append(
                f"  {i}. **Source:** `{c.get('source', '?')}`\n"
                f"     **Section:** `{c.get('section', '?')}`\n"
                f"     ```{snippet}...```"
            )
        parts.append("**Retrieved RAG chunks:**\n" + "\n".join(chunk_lines))
    else:
        parts.append("**Retrieved RAG chunks:** *None*")

    # --- Live Metrics ---
    metrics = trace.get("metrics", [])
    if metrics:
        metric_lines = []
        for m in metrics:
            scope = f"service={m.get('service', '')}"
            if m.get("endpoint"):
                scope += f",endpoint={m['endpoint']}"
            metric_lines.append(f"  {m['name']}{{{scope}}}: `{m['value']}`")
        parts.append("**Live metrics (sampled):**\n" + "\n".join(metric_lines))

    # --- Distributed trace (login -> ... -> logout) ---
    trace_summary = trace.get("trace_summary") or {}
    if trace_summary.get("total_traces"):
        t_lines = [
            f"  Journeys observed: `{trace_summary['total_traces']}` "
            f"(`{trace_summary['failed_traces']}` failed, "
            f"`{trace_summary['affected_users']}` user(s) affected)",
        ]
        for bp in trace_summary.get("break_points", [])[:5]:
            t_lines.append(
                f"    - `{bp['service']}{bp['endpoint']}` → `{bp['status_code']}` (×{bp['count']})"
            )
        sample_path = trace_summary.get("sample_path")
        trace_id = trace.get("trace_id")
        if sample_path and trace_id:
            path_str = " → ".join(f"{s['service']}{s['endpoint']}({s['status_code']})" for s in sample_path)
            t_lines.append(f"  Sample failed journey (`trace_id={trace_id}`):\n    {path_str}")
            t_lines.append(
                "  Pull every span of this journey in Grafana Explore (Loki):\n"
                "    ```logql\n"
                f'    {{source="incident-generator"}} | json | trace_id="{trace_id}"\n'
                "    ```"
            )
        parts.append("**Distributed traces (login → … → logout):**\n" + "\n".join(t_lines))

    # --- Log Analysis ---
    la = trace.get("log_analysis", {})
    if la and la.get("total_entries", 0) > 0:
        log_lines = [
            f"  Total entries: `{la['total_entries']}`",
            f"  Levels: {', '.join(f'{k}={v}' for k, v in sorted(la.get('by_level', {}).items()))}",
            f"  Error rate: `{la.get('error_rate_pct', 0)}%`",
        ]
        top_msgs = la.get("top_messages", [])
        if top_msgs:
            log_lines.append("  Top patterns:")
            for m in top_msgs[:5]:
                log_lines.append(f"    - `[{m['level']}]` \"{m['pattern']}\" ×{m['count']}")
        clusters = la.get("error_clusters", [])
        if clusters:
            log_lines.append(f"  Error clusters: `{len(clusters)}`")
        parts.append("**Log analysis:**\n" + "\n".join(log_lines))

    # --- Full Prompt (truncated for readability) ---
    prompt = trace.get("augmented_input", "")
    if prompt:
        prompt_display = prompt
        if len(prompt_display) > 2000:
            prompt_display = prompt_display[:2000] + "\n\n*... (truncated, full prompt sent to LLM)*"
        parts.append(
            "**Prompt sent to LLM:**\n"
            f"```text\n{prompt_display}\n```"
        )

    return "\n\n---\n\n".join(parts)


def _source_badge(live_source: str) -> str:
    """Map a trace data-source into the coloured header prepended to a reply."""
    if live_source == "live":
        logger.debug("triage badge: 🟢 Live")
        return "🟢 **Data source: Live (Prometheus + Loki)**\n\n"
    if live_source == "not_queried":
        logger.debug("triage badge: ⚪ Not queried")
        return "⚪ **Data source: Not queried — the agent answered without live telemetry**\n\n"
    logger.debug("triage badge: 🔴 Unavailable")
    return "🔴 **Data source: Unavailable — unable to reach Prometheus/Loki**\n\n"


def _title_from(message: str) -> str:
    """A short sidebar title from the first user message (first ~6 words)."""
    words = message.strip().split()
    title = " ".join(words[:6])
    if len(words) > 6:
        title += "…"
    return title or chat_store.DEFAULT_TITLE


def _display_content(m: dict) -> str:
    """Content as shown in the chatbot. The data-source badge is UI chrome and
    is NOT stored on the message (so it never pollutes the model's memory) --
    it's re-derived here from the assistant turn's stored trace source."""
    if m["role"] == "assistant" and m.get("trace_json"):
        try:
            source = json.loads(m["trace_json"]).get("source", "not_queried")
            return _source_badge(source) + m["content"]
        except (ValueError, TypeError):
            pass
    return m["content"]


def _chatbot_messages(chat_id: str | None) -> list[dict]:
    """A chat's stored messages as gr.Chatbot(type='messages') dicts."""
    if not chat_id:
        return []
    return [
        {"role": m["role"], "content": _display_content(m)}
        for m in chat_store.get_messages(chat_id)
    ]


def triage(incident_description: str, service: str, chat_id: str | None,
           sidebar_tick: int):
    """Handle one chat turn: persist the user message, run the agent with
    sliding-window memory, persist the reply + its trace, and return the
    updated chatbot, trace panel, active chat id, sidebar refresh tick, and a
    cleared input box.

    Returns a 5-tuple mapping to
    ``[chatbot, trace_output, current_chat_id, sidebar_tick, incident_input]``.
    """
    if not incident_description.strip():
        logger.debug("triage: empty input")
        # Nothing to do -- leave every component as-is.
        return (_chatbot_messages(chat_id), gr.update(), chat_id,
                sidebar_tick, gr.update())

    target = None if service in ("", "(all services)") else service

    # New conversation? Create it and title it from this first message.
    new_chat = chat_id is None
    if new_chat:
        chat_id = chat_store.create_chat(title=_title_from(incident_description))

    chat_store.append_message(chat_id, "user", incident_description)

    # Sliding-window memory: the last N completed pairs (the message we just
    # appended has no assistant reply yet, so recent_history won't include it).
    history = chat_store.recent_history(chat_id, HISTORY_WINDOW_TURNS)

    # Generate a unique request ID for this triage turn
    request_id = set_request_id()
    logger.info("Triage request [req=%s, chat=%s]: '%s...' (service=%s, history=%d turns)",
                request_id, chat_id, incident_description[:80], target or "all", len(history))

    # The agent itself decides whether/which telemetry tool(s) to call --
    # no pre-fetch here, so the badge below reflects what actually happened
    # this turn, not a fetch we forced regardless of the question.
    try:
        response = pilot.query(incident_description, service=target, history=history)
    except Exception as exc:
        # Without this, an exception here leaves Gradio's output components
        # showing whatever the *previous successful* query rendered, plus a
        # generic toast -- easy to misread as "this response errored" when
        # it's actually stale content from an earlier turn.
        logger.exception("Triage request [req=%s] failed", request_id)
        msg = str(exc)
        if isinstance(exc, ToolCallServiceError):
            # User-facing text deliberately omits the exception type/technical
            # detail (logged above via logger.exception for debugging) --
            # framed as Groq being slow under load, not an implementation bug.
            error_text = (
                f"⚠️ **Query failed** [req={request_id}]\n\n"
                "Groq's servers are responding slowly due to high traffic right now. "
                "Please try again."
            )
        else:
            hint = ""
            if any(s in msg for s in ("rate_limit", "429", "413", "tokens per")):
                hint = (
                    "\n\nThis is a Groq rate/size limit (per-minute or per-day token "
                    "budget), not a bug in the agent -- wait a bit and retry, or switch "
                    "`GROQ_MODEL` in `.env` if the daily quota is exhausted."
                )
            error_text = f"⚠️ **Query failed** [req={request_id}]\n\n`{type(exc).__name__}: {exc}`{hint}"
        error_trace = (
            f"**Request ID (this query):** `{request_id}`\n\n"
            "**Status:** failed before a response was produced -- see error above / container logs."
        )
        # Persist the failure as the assistant turn so the chat stays coherent.
        chat_store.append_message(chat_id, "assistant", error_text)
        bump = sidebar_tick + 1 if new_chat else sidebar_tick
        return (_chatbot_messages(chat_id), error_trace, chat_id, bump, "")

    trace = pilot.get_trace()
    live_source = trace.get("source", "not_queried")

    # Store the raw response only -- the data-source badge is re-derived from
    # the stored trace at display time (see _display_content), so the model's
    # sliding-window memory stays free of UI chrome.
    chat_store.append_message(chat_id, "assistant", response, trace=trace)

    # Build the trace panel (latest turn only)
    trace_md = _format_trace(trace)

    logger.info("Triage response [req=%s]: %d characters (source=%s, tool_calls=%s)",
                 request_id, len(response), live_source,
                 [t["name"] for t in trace.get("tool_calls", [])])

    # Only a brand-new chat changes the sidebar list; bump the tick to re-render.
    bump = sidebar_tick + 1 if new_chat else sidebar_tick
    return (_chatbot_messages(chat_id), trace_md, chat_id, bump, "")


def start_new_chat(sidebar_tick: int):
    """Reset to an empty, unsaved conversation (a chat row is created lazily on
    the first message). Returns [chatbot, trace_output, current_chat_id]."""
    logger.debug("start_new_chat")
    return [], "Run a triage query to see the agent's trace data.", None


def load_chat(chat_id: str):
    """Reopen a past chat: its messages + the trace of its most recent
    assistant turn. Returns [chatbot, trace_output, current_chat_id]."""
    logger.info("load_chat %s", chat_id)
    messages = chat_store.get_messages(chat_id)
    # Most recent assistant turn's stored trace, if any.
    trace_md = "No trace stored for this chat's turns yet."
    for m in reversed(messages):
        if m["role"] == "assistant" and m.get("trace_json"):
            try:
                trace_md = _format_trace(json.loads(m["trace_json"]))
            except (ValueError, TypeError):
                trace_md = "Stored trace could not be parsed."
            break
    return _chatbot_messages(chat_id), trace_md, chat_id


def _delete_and_refresh(target_id: str, active_id: str | None, sidebar_tick: int):
    """Delete a chat and refresh the sidebar. If the deleted chat is the one
    currently open, clear the conversation view too. Returns
    [chatbot, trace_output, current_chat_id, sidebar_tick]."""
    chat_store.delete_chat(target_id)
    if target_id == active_id:
        return ([], "Run a triage query to see the agent's trace data.",
                None, sidebar_tick + 1)
    # A different chat stays open; just re-render the list.
    return (_chatbot_messages(active_id), gr.update(), active_id, sidebar_tick + 1)


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

with gr.Blocks(
    title="IncidentPilot",
    theme=gr.themes.Soft(),
    css="""
        footer {display:none !important}
        pre { max-height: 300px; overflow-y: auto; font-size: 13px; }
        .incident-btn { min-width: 140px !important; }
        button.trigger-pool { background: #e74c3c !important; }
        button.trigger-cache { background: #f39c12 !important; }
        button.resolve-btn { background: #2ecc71 !important; }
        .state-card { border-left: 4px solid #3498db; padding: 10px; }
    """,
) as demo:
    gr.Markdown(
        "# 🚑 IncidentPilot\n"
        "AI-powered incident-response copilot for on-call SRE engineers, across a small "
        "simulated distributed system (`auth-service`, `listing-service`, `checkout-api`, "
        "`payment-service`). Describe a production incident to get a cited triage summary, "
        "or use the **Incident Control** tab to trigger scenarios manually."
    )

    with gr.Tabs():
        # ================================================================
        # TAB 1 — AI Triage
        # ================================================================
        with gr.TabItem("🚑 Triage"):
            # Per-browser-session state. Chat contents live in SQLite; State
            # holds only the active chat id and a tick that re-renders the
            # sidebar when the chat list changes.
            current_chat_id = gr.State(None)
            sidebar_tick = gr.State(0)

            with gr.Row():
                # ---------------- Sidebar: past chats ----------------
                # Header now; the chat list is rendered into this column
                # further down, once `chatbot`/`trace_output` (its click
                # targets) exist -- see the `with sidebar_col:` block below.
                with gr.Column(scale=1, min_width=200) as sidebar_col:
                    new_chat_btn = gr.Button("➕ New chat", variant="secondary", size="sm")
                    gr.Markdown("##### Past chats")

                # ---------------- Main: conversation ----------------
                with gr.Column(scale=4):
                    chatbot = gr.Chatbot(
                        label="Conversation",
                        type="messages",
                        height=480,
                        show_copy_button=True,
                    )
                    with gr.Row():
                        incident_input = gr.Textbox(
                            label="Incident description",
                            placeholder="e.g. checkout-api p99 latency has been climbing for 15 minutes...",
                            lines=3,
                            scale=4,
                            autofocus=True,
                        )
                        with gr.Column(scale=1, min_width=160):
                            # Hidden for the demo -- kept wired at
                            # "(all services)" so query() still works
                            # unchanged; the agent scopes telemetry itself
                            # from the incident description and its own tool
                            # judgement.
                            triage_service_dd = gr.Dropdown(
                                choices=TRIAGE_SERVICE_CHOICES,
                                value="(all services)",
                                label="Scope to service",
                                visible=False,
                            )
                            submit_btn = gr.Button("🚀 Triage", variant="primary", size="lg")

                    # Expandable trace panel (latest turn)
                    with gr.Accordion(label="🔍 Agent trace — show what the agent saw", open=False):
                        trace_output = gr.Markdown(
                            label="Agent trace",
                            value="Run a triage query to see the agent's trace data.",
                        )

                    # Current incident state (shared across tabs — always visible)
                    with gr.Accordion(label="📊 Current Incident State", open=False):
                        triage_state_output = gr.Markdown(
                            value=_get_state_markdown(),
                        )
                        refresh_state_btn = gr.Button(
                            "🔄 Refresh State",
                            variant="secondary",
                            size="sm",
                        )
                        refresh_state_btn.click(
                            fn=_refresh_state,
                            outputs=[triage_state_output],
                        )

                    gr.Examples(examples=EXAMPLE_QUERIES, inputs=incident_input)

            _triage_inputs = [incident_input, triage_service_dd, current_chat_id, sidebar_tick]
            _triage_outputs = [chatbot, trace_output, current_chat_id, sidebar_tick, incident_input]
            submit_btn.click(fn=triage, inputs=_triage_inputs, outputs=_triage_outputs)
            incident_input.submit(fn=triage, inputs=_triage_inputs, outputs=_triage_outputs)

            new_chat_btn.click(
                fn=start_new_chat,
                inputs=[sidebar_tick],
                outputs=[chatbot, trace_output, current_chat_id],
            )

            # Render the past-chats list into the sidebar column now that its
            # click targets (chatbot/trace_output) exist. Re-entering the
            # column as a context manager appends into it. `@gr.render`
            # re-executes whenever sidebar_tick / current_chat_id change, so a
            # new chat or a delete refreshes the list.
            with sidebar_col:
                @gr.render(inputs=[sidebar_tick, current_chat_id])
                def _render_sidebar(_tick, active_id):
                    chats = chat_store.list_chats()
                    if not chats:
                        gr.Markdown("_No chats yet — ask a question to start one._")
                        return
                    for c in chats:
                        marker = "▸ " if c["id"] == active_id else ""
                        with gr.Row():
                            open_btn = gr.Button(
                                f"{marker}{c['title']}",
                                size="sm",
                                variant="primary" if c["id"] == active_id else "secondary",
                                scale=5,
                            )
                            del_btn = gr.Button("🗑", size="sm", scale=1, min_width=40)
                        open_btn.click(
                            fn=load_chat,
                            inputs=gr.State(c["id"]),
                            outputs=[chatbot, trace_output, current_chat_id],
                        )
                        del_btn.click(
                            fn=_delete_and_refresh,
                            inputs=[gr.State(c["id"]), current_chat_id, sidebar_tick],
                            outputs=[chatbot, trace_output, current_chat_id, sidebar_tick],
                        )

        # ================================================================
        # TAB 2 — Incident Control
        # ================================================================
        with gr.TabItem("🎮 Incident Control"):
            gr.Markdown(
                "### Trigger or resolve incidents\n\n"
                "Pick a target service (or leave the kind's default) and use the buttons below "
                "to simulate production incidents directly. Each button calls the generator's "
                "API — the same endpoints you'd use with `curl`. After triggering, watch metrics "
                "update in Grafana or poll the state below."
            )

            service_dd = gr.Dropdown(
                choices=SERVICE_CHOICES,
                value="(kind default)",
                label="Target service",
            )
            capability_hint = gr.Markdown(value=_service_capability_hint("(kind default)"))
            service_dd.change(
                fn=_service_capability_hint,
                inputs=[service_dd],
                outputs=[capability_hint],
            )

            with gr.Row():
                pool_btn = gr.Button(
                    "🔴 Pool Exhaustion",
                    elem_classes="incident-btn trigger-pool",
                    size="lg",
                )
                cache_btn = gr.Button(
                    "🟡 Cache Failover",
                    elem_classes="incident-btn trigger-cache",
                    size="lg",
                )

            with gr.Row():
                resolve_btn = gr.Button(
                    "🟢 Resolve Current",
                    elem_classes="incident-btn resolve-btn",
                    size="lg",
                    scale=1,
                )
                refresh_btn = gr.Button(
                    "🔄 Refresh State",
                    variant="secondary",
                    size="lg",
                    scale=1,
                )

            status_output = gr.Markdown(
                label="Last action",
                value="ℹ️ Click a trigger button or **Refresh State** to begin.",
            )

            state_output = gr.Markdown(
                label="Current state",
                value=_get_state_markdown(),
            )

            pool_btn.click(
                fn=lambda svc: _trigger("pool", svc, True),
                inputs=[service_dd],
                outputs=[status_output, state_output],
            )
            cache_btn.click(
                fn=lambda svc: _trigger("cache", svc, True),
                inputs=[service_dd],
                outputs=[status_output, state_output],
            )
            resolve_btn.click(
                fn=_resolve_current,
                inputs=[service_dd],
                outputs=[status_output, state_output],
            )
            refresh_btn.click(
                fn=_refresh_state,
                outputs=[state_output],
            )


if __name__ == "__main__":
    setup_logging()
    configure_observability()
    logger.info("Starting Gradio UI on port 7860")
    demo.launch(share=False, server_name="0.0.0.0", server_port=7860)
