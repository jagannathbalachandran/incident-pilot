"""
IncidentPilot Gradio UI — Week 1, Task 9.

An engineer types an incident description and gets a RAG-grounded, cited triage
summary back from IncidentPilot.query() — enriched with live Prometheus/Loki
metrics (across every simulated service) when the monitoring stack is running.

Includes an expandable **trace panel** showing the agent's reasoning: which RAG
chunks were retrieved, what live data (including any reconstructed user
journey) was returned, and the full prompt sent to the LLM.

Tab 2: **Incident Control** lets you trigger/resolve scenarios directly from the
Gradio UI without using curl — pool exhaustion, cache failover, fraud outage,
or a random incident, optionally targeting a specific service.

Usage:
    python src/app.py
"""

import logging
import os

import gradio as gr
import requests

from incident_pilot import IncidentPilot
from logging_config import setup_logging
from request_context import set_request_id

logger = logging.getLogger(__name__)

pilot = IncidentPilot()

# Incident-generator API base URL (Docker host port — override via env var)
_FLASK_API = os.getenv("FLASK_API_URL", "http://localhost:5001")

# Static fallback if the generator isn't reachable yet when the UI starts.
_FALLBACK_SERVICES = ["auth-service", "listing-service", "checkout-api", "payment-service"]


def _fetch_service_names() -> list:
    """Fetch the topology's service names from the generator, for the
    service-selector dropdown. Falls back to a static list if unreachable."""
    try:
        resp = requests.get(f"{_FLASK_API}/api/services", timeout=5)
        data = resp.json()
        names = [s["name"] for s in data.get("services", []) if s.get("user_facing")]
        if names:
            return names
    except Exception as exc:
        logger.warning("Failed to fetch service list, using static fallback: %s", exc)
    return list(_FALLBACK_SERVICES)


SERVICE_CHOICES = ["(kind default)"] + _fetch_service_names()


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


def _trigger_random_incident():
    """Trigger a randomly selected incident on a randomly selected supporting service."""
    logger.info("Triggering random incident")
    try:
        resp = requests.post(f"{_FLASK_API}/api/incidents/trigger-random", timeout=5)
        data = resp.json()
    except Exception as exc:
        logger.warning("Random trigger failed: %s", exc)
        msg = f"❌ **Trigger error:** `{exc}`"
        return msg, _get_state_markdown()

    kind = data.get("kind", "?")
    service = data.get("service", "?")
    rid = data.get("request_id", "-")
    phase = data.get("phase", "?")

    msg = (
        f"🎲 **Random incident triggered!**\n\n"
        f"| Field | Value |\n"
        f"|---|---|\n"
        f"| **Kind** | `{kind}` |\n"
        f"| **Service** | `{service}` |\n"
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
    "API latency spiked 5x in the last 15 minutes, what's going on?",
    "What does the runbook say to do for a connection-pool exhaustion?",
    "checkout-api p99 latency has been climbing gradually over the last 15 minutes, no sudden step change. What's going on and what does the runbook say to do?",
    "A user says they logged in, browsed listings, but checkout failed — can you trace what happened to their session?",
    "payment-service looks slow — is that affecting checkout-api too?",
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
    elif source == "static_fallback":
        source_badge = "🟡 **Static files (fallback)**"
    else:
        source_badge = "🔴 **Unavailable**"
    parts.append(f"**Data source:** {source_badge}")

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


def triage(incident_description: str, service: str = "(all services)"):
    """Process a triage query and return (response_text, trace_markdown)."""
    if not incident_description.strip():
        logger.debug("triage: empty input")
        return "Please describe the incident you're triaging.", ""

    target = None if service in ("", "(all services)") else service

    # Generate a unique request ID for this triage session
    request_id = set_request_id()
    logger.info("Triage request [req=%s]: '%s...' (service=%s)",
                request_id, incident_description[:80], target or "all")

    # Query live data once, then pass the result to query()
    logs_result = pilot.query_logs(timeframe="15m", service=target)
    live_source = logs_result.get("source", "unavailable")
    response = pilot.query(
        incident_description, live_data_timeframe="15m", logs_result=logs_result, service=target,
    )

    # Build the data-source badge
    if live_source == "live":
        badge = "🟢 **Data source: Live (Prometheus + Loki)**\n\n"
        logger.debug("triage badge: 🟢 Live")
    elif live_source == "static_fallback":
        badge = "🟡 **Data source: Static files (fallback)**\n\n"
        logger.debug("triage badge: 🟡 Static fallback")
    else:
        badge = "🔴 **Data source: Unavailable**\n\n"
        logger.debug("triage badge: 🔴 Unavailable")

    # Build the trace panel
    trace = pilot.get_trace()
    trace_md = _format_trace(trace)

    logger.info("Triage response [req=%s]: %d characters (source=%s)",
                 request_id, len(response), live_source)
    return badge + response, trace_md


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
        button.trigger-fraud { background: #9b59b6 !important; }
        button.trigger-random { background: #3498db !important; }
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
            with gr.Row():
                with gr.Column(scale=4):
                    incident_input = gr.Textbox(
                        label="Incident description",
                        placeholder="e.g. checkout-api p99 latency has been climbing for 15 minutes...",
                        lines=4,
                    )
                with gr.Column(scale=1, min_width=160):
                    triage_service_dd = gr.Dropdown(
                        choices=["(all services)"] + _fetch_service_names(),
                        value="(all services)",
                        label="Scope to service",
                    )
                    submit_btn = gr.Button("🚀 Triage", variant="primary", size="lg")

            summary_output = gr.Markdown(label="Triage summary")

            # Expandable trace panel
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

            submit_btn.click(
                fn=triage,
                inputs=[incident_input, triage_service_dd],
                outputs=[summary_output, trace_output],
            )
            incident_input.submit(
                fn=triage,
                inputs=[incident_input, triage_service_dd],
                outputs=[summary_output, trace_output],
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
                fraud_btn = gr.Button(
                    "🟣 Fraud Outage",
                    elem_classes="incident-btn trigger-fraud",
                    size="lg",
                )
                random_btn = gr.Button(
                    "🔵 Random",
                    elem_classes="incident-btn trigger-random",
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
            fraud_btn.click(
                fn=lambda svc: _trigger("fraud", svc, True),
                inputs=[service_dd],
                outputs=[status_output, state_output],
            )
            random_btn.click(
                fn=_trigger_random_incident,
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
    logger.info("Starting Gradio UI on port 7860")
    demo.launch(share=False, server_name="0.0.0.0", server_port=7860)
