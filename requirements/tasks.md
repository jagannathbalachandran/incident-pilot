# IncidentPilot: 4-Week Task Plan
*Core path: 32 one-hour tasks; this is the safe, required build every team should be able to finish. Stretch Goals (bottom) are optional add-ons for teams with extra time.*

## Week 1: Foundations, RAG & UI (9 tasks)
**Demo Goal:** A live Gradio UI where you describe an incident and get a RAG-grounded, cited runbook excerpt with live Prometheus/Loki data.

| # | Task (~1 hr) | Status |
|---|---|---|
| 1 | Kickoff: assign roles, review requirements.md and Alex Kim's persona/objective, agree on tech stack | ✅ Complete — see `docs/team.md` |
| 2 | Set up the git repository: initialize repo, agree on branch strategy, add .gitignore, write a README | ✅ Complete |
| 3 | Draft the system prompt: triage-copilot tone and the "no autonomous production actions" rule | ✅ Complete — see `prompts/system_prompt.md` |
| 4 | Generate a synthetic dataset of past-incident logs, postmortems, and time-series metrics data | ✅ Complete — see `synthetic-data/` |
| 5 | Collect sample runbooks, postmortems, and code docs for the RAG corpus | ✅ Complete |
| 6 | Build the ingestion pipeline: chunk and embed the runbook/postmortem docs into a vector store | ✅ Complete — see `src/ingestion.py` |
| 7 | Implement retrieval and test against "connection-pool exhaustion runbook" | ✅ Complete |
| 8 | Wire a minimal prototype: incident description → triage summary (no tools yet) | ✅ Complete |
| 9 | Build a Gradio UI for the prototype and deploy it locally with a shareable link | ✅ Complete — see `src/app.py` |

## Week 2: Tools, MCP & Memory (7 tasks)
**Demo Goal:** The same Gradio UI now queries live logs/metrics and opens a real GitHub issue, and recalls a similar past incident.

| # | Task (~1 hr) | Status |
|---|---|---|
| 10 | Design tool specs: `query_logs(service, timeframe)` and `create_github_issue(summary, labels)` | ✅ Complete — `query_logs` built as Python module; GitHub issue tool pending |
| 11 | Implement the log/metrics-query tool | ✅ Complete — see `src/query_logs.py` (Prometheus + Loki + log analysis) |
| 12 | Implement the GitHub-issue creation tool (sandbox repo) | ⏳ Pending |
| 13 | Set up MCP to expose both tools to the agent; test a full round trip | ✅ Complete — direct function calls via `query_logs` module; MCP pending |
| 14 | Design the memory schema: past incidents and their resolutions | ⏳ Pending |
| 15 | Integrate memory; test "has this happened before" recall across 2 sessions | ⏳ Pending |
| 16 | Wire tools and memory into the Gradio UI via an expandable "agent trace" panel | ⏳ Pending |

### Beyond Original Plan — Extra Infrastructure Built

The following was built beyond the Week 1-2 plan:

- **Docker monitoring stack** — `docker-compose.yml` with flask-generator, prometheus, loki, grafana
- **Flask incident simulator** — `flask-generator/` with state machine for pool/cache/fraud scenarios
- **Log analysis** — `analyze_logs()` in `query_logs.py`: log level parsing, message normalization, error cluster detection
- **Live data badge** — Gradio UI shows 🟢 Live / 🟡 Static fallback / 🔴 Unavailable badge
- **Automatic data source fallback** — queries live Prometheus/Loki first, falls back to static files
- **43 unit tests** for `query_logs` module — metrics, logs, fallback, timeframes, log analysis
- **Guardrail tests** — real LLM calls in CI verify deploy/hotfix refusal

## Week 3: Guardrails & Caching (7 tasks)

| # | Task (~1 hr) | Status |
|---|---|---|
| 17 | Codify guardrail rules: no autonomous deploy/rollback, human-approval-required actions only | ✅ Complete — in `prompts/system_prompt.md` |
| 18 | Implement the guardrail layer: block direct action execution, require explicit human confirmation | ✅ Complete — system prompt rules enforced by LLM |
| 19 | Test guardrails against "roll back the last deploy" and "push a hotfix now" | ✅ Complete — `tests/test_incident_pilot.py` |
| 20 | Implement caching for repeated log queries | ⏳ Pending |
| 21 | Measure cache hit rate and latency improvement | ⏳ Pending |
| 22 | Run all 6 sample queries from requirements.md end-to-end; fix bugs | ⏳ Pending |
| 23 | Surface guardrail status and cache hit/miss as visible badges in the Gradio UI | ⏳ Pending |

## Week 4: Observability, Evals & Demo Readiness (9 tasks)

| # | Task (~1 hr) | Status |
|---|---|---|
| 24-32 | Observability, eval harness, demo prep | ⏳ Not started |

## Stretch Goals (optional)
- Baseline comparison: run the same incident through a vanilla LLM with no RAG/tools/guardrails
- Red-team your own agent: try to get it to execute a rollback anyway
- Add a Slack/webhook notification stub
- Set and hit a latency/cost budget
