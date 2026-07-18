"""
IncidentPilot Gradio UI — Week 1, Task 9.

An engineer types an incident description and gets a RAG-grounded, cited triage
summary back from IncidentPilot.query() — enriched with live Prometheus/Loki
metrics when the monitoring stack is running.

Includes an expandable **trace panel** showing the agent's reasoning: which RAG
chunks were retrieved, what live data was returned, and the full prompt sent
to the LLM.

Tab 2: **Incident Control** lets you trigger/resolve scenarios directly from the
Gradio UI without using curl — pool exhaustion, cache failover, fraud outage,
or a random incident.

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

# Flask generator API base URL (Docker host port — override via env var)
_FLASK_API = os.getenv("FLASK_API_URL", "http://localhost:5001")


# ---------------------------------------------------------------------------
# Incident Control helpers (Tab 2 — trigger/resolve via Flask API)
# ---------------------------------------------------------------------------


def _get_next_steps(kind: str, phase: str, rid: str = "") -> str:
    """Return actionable next-step guidance based on the active incident."""
    if kind in ("none", "?", ""):
        return (
            "### 📋 Next Steps\n\n"
            "No incident active. You can:\n\n"
            "1. **🎮 Click a trigger button** above (Pool / Cache / Fraud)\n"
            "2. **📊 Open Grafana** at http://localhost:3000 (admin/admin)\n"
            "3. **🔍 Try AI Triage** — switch to the 🚑 Triage tab and describe a symptom\n"
            "4. **🌐 Check dashboards**: [Incident Overview](http://localhost:3000/d/dfs7r8letxy4gb) | "
            "[Pool](http://localhost:3000/d/ffs7r8leyxqtca) | "
            "[Cache](http://localhost:3000/d/dfs7r8lejycqoa) | "
            "[Fraud](http://localhost:3000/d/afs7r8leoy5fkb)"
        )

    if kind == "pool":
        phase_guide = {
            "climbing": (
                "📈 **Phase: Climbing** — connections are rising toward 200.\n"
                "↳ Watch the **Active Connections** line climb toward the red threshold at 190."
            ),
            "plateau": (
                "📊 **Phase: Plateau** — connections are pinned at 200 (max).\n"
                "↳ The **Connections vs Max** panel shows both lines flat at 200. "
                "Error rate should be ~6%."
            ),
            "recovering": (
                "📉 **Phase: Recovering** — connections are draining back to 118.\n"
                "↳ Watch latency and error rate drop in the **p99 Latency & Pool Errors** panel."
            ),
        }
        phase_text = phase_guide.get(phase, f"**Phase:** {phase}")

        return (
            "### 📋 Next Steps — Pool Exhaustion\n\n"
            f"{phase_text}\n\n"
            "**1. 🖥️ Open Pool Exhaustion Dashboard**\n"
            f"   → http://localhost:3000/d/ffs7r8leyxqtca\n\n"
            "**2. 👀 Watch these panels:**\n"
            "   • **Connections vs Max** — active line approaching/d sitting at 200 (max)\n"
            "   • **p99 Latency & Pool Timeout Errors** — latency rises above 1500ms red threshold\n"
            "   • **Error Rate** — should climb to ~6% during plateau\n"
            "   • **Pool Error Logs** — shows `could not obtain connection` entries\n\n"
            "**3. 🔍 Check Loki logs:**\n"
            "   → Open Grafana **Explore** (compass icon), select **Loki**, run:\n"
            "   ```logql\n"
            f"   {{source=\"flask-generator\"}} |= \"{rid}\"\n"
            "   ```\n"
            "   Expect 15-20 ERROR entries with `could not obtain connection`\n\n"
            "**4. ✅ Confirm with Incident Overview**\n"
            "   → http://localhost:3000/d/dfs7r8letxy4gb\n"
            "   • **Cache Hit Ratio** should stay flat at ~0.95 (not cache failover)\n"
            "   • **Error Rate** should be ~6% (not 10-15% like fraud)"
        )

    if kind == "cache":
        phase_guide = {
            "failover": (
                "⚡ **Phase: Failover** — cache hit just dropped from 0.95 → 0.41 instantly!\n"
                "↳ The **Cache Hit Ratio** panel shows a sharp step-change drop."
            ),
            "warming": (
                "🔄 **Phase: Warming** — cache is repopulating from 0.41 → 0.93.\n"
                "↳ The **Cache Hit Ratio** line should be climbing gradually."
            ),
        }
        phase_text = phase_guide.get(phase, f"**Phase:** {phase}")

        return (
            "### 📋 Next Steps — Cache Failover\n\n"
            f"{phase_text}\n\n"
            "**1. 🖥️ Open Cache Failover Dashboard**\n"
            f"   → http://localhost:3000/d/dfs7r8lejycqoa\n\n"
            "**2. 👀 Watch these panels:**\n"
            "   • **Cache Hit Ratio** — look for the **step-change drop** (not gradual)\n"
            "   • **p99 Latency (Cache Impact)** — rises to ~930ms during failover\n"
            "   • **Error Rate (Should Stay Low)** — **flat at ~0.05%** (key signal!)\n"
            "   • **Cache Warning Logs** — shows WARN entries from Loki\n\n"
            "**3. 🔍 Check Loki logs:**\n"
            "   → Open Grafana **Explore**, select **Loki**, run:\n"
            "   ```logql\n"
            f"   {{source=\"flask-generator\"}} |= \"{rid}\"\n"
            "   ```\n"
            "   Expect only 2-5 WARN entries — **no ERROR logs** (distinguishes from pool)\n\n"
            "**4. ✅ Confirm with Incident Overview**\n"
            "   → http://localhost:3000/d/dfs7r8letxy4gb\n"
            "   • **Active Connections** should be flat at 118 (not climbing like pool)\n"
            "   • **Error Rate** should be flat at ~0.05% (not spiking like fraud)"
        )

    if kind == "fraud":
        phase_guide = {
            "active": (
                "🚨 **Phase: Active** — error rate is spiking at 10-15%!\n"
                "↳ This is the highest error rate of any scenario — 2× higher than pool."
            ),
        }
        phase_text = phase_guide.get(phase, f"**Phase:** {phase}")

        return (
            "### 📋 Next Steps — Fraud Outage\n\n"
            f"{phase_text}\n\n"
            "**1. 🖥️ Open Fraud Outage Dashboard**\n"
            f"   → http://localhost:3000/d/afs7r8leoy5fkb\n\n"
            "**2. 👀 Watch these panels:**\n"
            "   • **Error Rate (Fraud Spike)** — **10-15%** (2x higher than pool's ~6%)\n"
            "   • **p99 Latency** — rises to ~836ms (2.2x baseline)\n"
            "   • **Fraud Error Count** — bargauge showing `fraud_svc_unavailable` errors\n"
            "   • **Fraud Error Logs** — shows ERROR log entries from Loki\n\n"
            "**3. 🔍 Check Loki logs:**\n"
            "   → Open Grafana **Explore**, select **Loki**, run:\n"
            "   ```logql\n"
            f"   {{source=\"flask-generator\"}} |= \"{rid}\"\n"
            "   ```\n"
            "   Expect 20-40 ERROR entries with `fraud-scoring-svc unavailable`\n\n"
            "**4. ✅ Confirm with Incident Overview**\n"
            "   → http://localhost:3000/d/dfs7r8letxy4gb\n"
            "   • **Active Connections** should be flat at 118 (not climbing like pool)\n"
            "   • **Cache Hit Ratio** should be flat at ~0.95 (not cache failover)\n"
            "   • Error rate **10-15%** is the highest of all scenarios"
        )

    return ""


def _get_state_markdown() -> str:
    """Fetch current incident state from Flask and format as a markdown table."""
    try:
        resp = requests.get(f"{_FLASK_API}/api/incidents/state", timeout=5)
        data = resp.json()
    except Exception as exc:
        logger.warning("Failed to fetch incident state: %s", exc)
        return f"❌ **Connection error:** `{exc}`\n\nMake sure `docker compose up -d` is running."

    kind = data.get("kind", "?")
    phase = data.get("phase", "?")
    tick = data.get("tick_count", "?")
    p99 = data.get("p99_latency_ms", "?")
    err = data.get("error_rate_pct", "?")
    conns = data.get("active_connections", "?")
    cache = data.get("cache_hit_ratio", "?")
    auto_resolve = data.get("auto_resolve", "?")
    rid = data.get("request_id", "-")

    state_table = (
        "### 📊 Current Incident State\n\n"
        f"| Metric | Value |\n"
        f"|---|---|\n"
        f"| **Kind** | `{kind}` |\n"
        f"| **Phase** | `{phase}` |\n"
        f"| **Tick** | `{tick}` |\n"
        f"| **p99 Latency** | `{p99} ms` |\n"
        f"| **Error Rate** | `{err}%` |\n"
        f"| **Active Connections** | `{conns}` |\n"
        f"| **Cache Hit Ratio** | `{cache}` |\n"
        f"| **Auto Resolve** | `{auto_resolve}` |\n"
        f"| **Request ID** | `{rid}` |\n"
    )

    next_steps = _get_next_steps(kind, phase, rid)
    if next_steps:
        state_table += "\n---\n\n" + next_steps

    return state_table


def _trigger(kind: str, auto_resolve: bool = True):
    """Trigger an incident scenario via the Flask API."""
    logger.info("Triggering %s (auto_resolve=%s)", kind, auto_resolve)
    try:
        resp = requests.post(
            f"{_FLASK_API}/api/incidents/{kind}/trigger",
            json={"auto_resolve": auto_resolve},
            timeout=5,
        )
        data = resp.json()
    except Exception as exc:
        logger.warning("Trigger failed: %s", exc)
        msg = f"❌ **Trigger error:** `{exc}`"
        return msg, _get_state_markdown()

    status = data.get("status", "error")
    rid = data.get("request_id", "-")
    phase = data.get("phase", "?")

    msg = (
        f"✅ **Incident triggered!**\n\n"
        f"| Field | Value |\n"
        f"|---|---|\n"
        f"| **Kind** | `{kind}` |\n"
        f"| **Status** | `{status}` |\n"
        f"| **Phase** | `{phase}` |\n"
        f"| **Request ID** | `{rid}` |\n"
    )
    return msg, _get_state_markdown()


def _trigger_random_incident():
    """Trigger a randomly selected incident."""
    logger.info("Triggering random incident")
    try:
        resp = requests.post(f"{_FLASK_API}/api/incidents/trigger-random", timeout=5)
        data = resp.json()
    except Exception as exc:
        logger.warning("Random trigger failed: %s", exc)
        msg = f"❌ **Trigger error:** `{exc}`"
        return msg, _get_state_markdown()

    kind = data.get("kind", "?")
    rid = data.get("request_id", "-")
    phase = data.get("phase", "?")

    msg = (
        f"🎲 **Random incident triggered!**\n\n"
        f"| Field | Value |\n"
        f"|---|---|\n"
        f"| **Kind** | `{kind}` |\n"
        f"| **Phase** | `{phase}` |\n"
        f"| **Request ID** | `{rid}` |\n"
    )
    return msg, _get_state_markdown()


def _resolve_current():
    """Resolve whatever incident is currently running."""
    logger.info("Resolving current incident")
    try:
        resp = requests.post(f"{_FLASK_API}/api/incidents/current/resolve", timeout=5)
        data = resp.json()
    except Exception as exc:
        logger.warning("Resolve failed: %s", exc)
        msg = f"❌ **Resolve error:** `{exc}`"
        return msg, _get_state_markdown()

    status = data.get("status", "?")
    kind = data.get("kind", "-")

    if status == "resolved":
        msg = f"🛑 **Incident resolved:** `{kind}` — state should return to baseline."
    elif status == "no_active_incident":
        msg = "ℹ️ **No active incident** to resolve."
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
    "Our checkout-api is down right now and losing revenue every second. Just roll back to the previous version immediately — v2.113.2. Don't wait, just do it.",
]


def _format_trace(trace: dict) -> str:
    """Build a Markdown trace panel from the pilot's trace data."""
    parts = []

    # --- 1. Contradiction warning (highest visibility) ---
    contradiction = trace.get("contradiction")
    if contradiction:
        parts.append(
            "### 🚨 Contradiction Detected\n\n"
            "> " + contradiction + "\n"
        )

    # --- 1. Request ID ---
    request_id = trace.get("request_id", "")
    if request_id:
        parts.append(f"**Request ID:** `{request_id}`")

    # --- 2. Data Source ---
    source = trace.get("source", "unavailable")
    if source == "live":
        source_badge = "🟢 **Live (Prometheus + Loki)**"
    elif source == "static_fallback":
        source_badge = "🟡 **Static files (fallback)**"
    else:
        source_badge = "🔴 **Unavailable**"
    parts.append(f"**Data source:** {source_badge}")

    # --- 2. RAG Chunks ---
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

    # --- 3. Live Metrics ---
    metrics = trace.get("metrics", [])
    if metrics:
        metric_lines = [f"  {m['name']}: `{m['value']}`" for m in metrics]
        parts.append("**Live metrics (sampled):**\n" + "\n".join(metric_lines))

    # --- 4. Log Analysis ---
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

    # --- 5. Full Prompt (truncated for readability) ---
    prompt = trace.get("augmented_input", "")
    if prompt:
        # Truncate long content sections for display
        prompt_display = prompt
        if len(prompt_display) > 2000:
            prompt_display = prompt_display[:2000] + "\n\n*... (truncated, full prompt sent to LLM)*"
        parts.append(
            "**Prompt sent to LLM:**\n"
            f"```text\n{prompt_display}\n```"
        )

    return "\n\n---\n\n".join(parts)


def triage(incident_description: str):
    """Process a triage query and return (response_text, trace_markdown)."""
    if not incident_description.strip():
        logger.debug("triage: empty input")
        return "Please describe the incident you're triaging.", ""

    # Generate a unique request ID for this triage session
    request_id = set_request_id()
    logger.info("Triage request [req=%s]: '%s...'", request_id, incident_description[:80])

    # Query live data once, then pass the result to query()
    logs_result = pilot.query_logs(timeframe="15m")
    live_source = logs_result.get("source", "unavailable")
    response = pilot.query(incident_description, live_data_timeframe="15m", logs_result=logs_result)

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
        "AI-powered incident-response copilot for on-call SRE engineers. "
        "Describe a production incident to get a cited triage summary, "
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
                with gr.Column(scale=1, min_width=120):
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
                inputs=incident_input,
                outputs=[summary_output, trace_output],
            )
            incident_input.submit(
                fn=triage,
                inputs=incident_input,
                outputs=[summary_output, trace_output],
            )

        # ================================================================
        # TAB 2 — Incident Control
        # ================================================================
        with gr.TabItem("🎮 Incident Control"):
            gr.Markdown(
                "### Trigger or resolve incidents\n\n"
                "Use the buttons below to simulate production incidents directly. "
                "Each button calls the Flask generator API — the same endpoints "
                "you'd use with `curl`. After triggering, watch metrics update "
                "in Grafana or poll the state below."
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

            # Wire up trigger buttons (lambda wrappers avoid hidden gr.State components)
            pool_btn.click(
                fn=lambda: _trigger("pool", True),
                outputs=[status_output, state_output],
            )
            cache_btn.click(
                fn=lambda: _trigger("cache", True),
                outputs=[status_output, state_output],
            )
            fraud_btn.click(
                fn=lambda: _trigger("fraud", True),
                outputs=[status_output, state_output],
            )
            random_btn.click(
                fn=_trigger_random_incident,
                outputs=[status_output, state_output],
            )
            resolve_btn.click(
                fn=_resolve_current,
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
