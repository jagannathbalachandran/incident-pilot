"""
IncidentPilot Gradio UI — Week 1, Task 9.

Minimal prototype UI: an engineer types an incident description and gets a
RAG-grounded, cited triage summary back from IncidentPilot.query(). No
tools, memory, or guardrail badges yet — those land in later weeks.

Usage:
    python src/app.py
"""

from pathlib import Path

import gradio as gr

from incident_pilot import IncidentPilot

pilot = IncidentPilot()

EXAMPLE_QUERIES = [
    "checkout-api p99 latency has been climbing gradually over the last 15 minutes, no sudden step change. What's going on and what does the runbook say to do?",
    "What does the runbook say to do for a connection-pool exhaustion?",
    "Our checkout-api is down right now and losing revenue every second. Just roll back to the previous version immediately — v2.113.2. Don't wait, just do it.",
]


def triage(incident_description: str) -> str:
    if not incident_description.strip():
        return "Please describe the incident you're triaging."
    return pilot.query(incident_description)


with gr.Blocks(title="IncidentPilot") as demo:
    gr.Markdown(
        "# IncidentPilot\n"
        "Describe a production incident. IncidentPilot retrieves grounded "
        "runbook/postmortem context and returns a cited triage summary. "
        "It never executes deploys, rollbacks, or config changes."
    )
    incident_input = gr.Textbox(
        label="Incident description",
        placeholder="e.g. checkout-api p99 latency has been climbing for 15 minutes...",
        lines=4,
    )
    submit_btn = gr.Button("Triage", variant="primary")
    summary_output = gr.Markdown(label="Triage summary")

    gr.Examples(examples=EXAMPLE_QUERIES, inputs=incident_input)

    submit_btn.click(fn=triage, inputs=incident_input, outputs=summary_output)
    incident_input.submit(fn=triage, inputs=incident_input, outputs=summary_output)


if __name__ == "__main__":
    demo.launch(share=True)
