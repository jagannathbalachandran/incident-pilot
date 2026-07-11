# IncidentPilot: 4-Week Task Plan
*Core path: 32 one-hour tasks; this is the safe, required build every team should be able to finish. Stretch Goals (bottom) are optional add-ons for teams with extra time.*

## Week 1: Foundations, RAG & UI (9 tasks)
**Demo Goal:** A live Gradio UI where you describe an incident and get a RAG-grounded, cited runbook excerpt; no tools, memory, or guardrails yet, but it's clickable and shareable.

| # | Task (~1 hr) | Definition of Done | Evidence of Completion |
|---|---|---|---|
| 1 | Kickoff: assign roles, review requirements.md and Alex Kim's persona/objective, agree on tech stack | Roles assigned (prompt/RAG, tools/MCP, memory, guardrails/caching, observability/UI owners); requirements.md read by everyone; stack agreed | A `docs/team.md` listing roles and stack, with each member confirming they've read requirements.md |
| 2 | Set up the git repository: initialize repo, agree on branch strategy, add .gitignore, write a README | Repo exists remotely with main + feature branches; README lets a fresh clone run the project | A teammate clones the repo and runs it successfully from README alone |
| 3 | Draft the system prompt: triage-copilot tone and the "no autonomous production actions" rule | Prompt file committed; 2 manual test prompts show the correct tone and rule | Prompt file in repo + pasted transcript of the 2 test runs |
| 4 | Generate a synthetic dataset of past-incident logs, postmortems, and time-series metrics data | Dataset committed covering at least one incident matching the connection-pool exhaustion scenario | Dataset file in repo + a summary noting which incident matches the test query |
| 5 | Collect sample runbooks, postmortems, and code docs for the RAG corpus | Corpus covers all 6 sample queries in requirements.md, especially the connection-pool runbook | Corpus folder committed with a source list |
| 6 | Build the ingestion pipeline: chunk and embed the runbook/postmortem docs into a vector store | Pipeline runs with no errors; vector store has the expected chunk count | Console log showing chunk/embedding count |
| 7 | Implement retrieval and test against "connection-pool exhaustion runbook" | Relevant runbook chunk(s) appear in the top-3 retrieved results | Logged query + retrieved chunks with a correct/incorrect judgment |
| 8 | Wire a minimal prototype: incident description → triage summary (no tools yet) | Full description→summary round trip runs without crashing and cites the runbook source | Terminal/notebook transcript of one successful run |
| 9 | Build a Gradio UI for the prototype and deploy it locally with a shareable link | Gradio app launches and returns a grounded triage summary for a real incident description | Screenshot of the running UI + shareable link posted to the team channel |

## Week 2: Tools, MCP & Memory (7 tasks)
**Demo Goal:** The same Gradio UI now queries live logs/metrics and opens a real GitHub issue, and recalls a similar past incident; visible live in the UI.

| # | Task (~1 hr) | Definition of Done | Evidence of Completion |
|---|---|---|---|
| 10 | Design tool specs: `query_logs(service, timeframe)` and `create_github_issue(summary, labels)` | Written spec for both tools: inputs, outputs, error cases | `docs/tools.md` with both signatures and example input/output |
| 11 | Implement the log/metrics-query tool | Returns correct sample metrics for a known service/timeframe and a clear error otherwise | Test log showing both cases |
| 12 | Implement the GitHub-issue creation tool (sandbox repo) | Creates an issue and returns its URL for at least one test input | Test log showing the call and returned issue URL |
| 13 | Set up MCP to expose both tools to the agent; test a full round trip | Agent calls both tools via MCP and uses their results in a live response | Trace/log of one query showing the response built from tool output |
| 14 | Design the memory schema: past incidents and their resolutions | Schema documented; a record can be written and read back correctly | Schema doc + log of one record written and retrieved |
| 15 | Integrate memory; test "has this happened before" recall across 2 sessions | A past incident logged in session 1 is correctly recalled, unprompted, in session 2 | Transcripts of both sessions showing the incident and its recall |
| 16 | Wire tools and memory into the Gradio UI via an expandable "agent trace" panel | Panel lists each tool call and the recalled past incident used | Screenshot of the panel expanded on a real query |

## Week 3: Guardrails & Caching (7 tasks)
**Demo Goal:** In the live UI, show the agent refuse to execute a rollback/hotfix and instead require human confirmation, and show a visible speed-up (cache hit badge) on a repeated log query.

| # | Task (~1 hr) | Definition of Done | Evidence of Completion |
|---|---|---|---|
| 17 | Codify guardrail rules: no autonomous deploy/rollback, human-approval-required actions only | Rules written as a checklist mapped to requirements.md's guardrail section | `docs/guardrails.md` listing each rule with its requirements.md reference |
| 18 | Implement the guardrail layer: block direct action execution, require explicit human confirmation | Every action request passes through the guardrail check before execution | Log entry showing a rollback request blocked pending human confirmation |
| 19 | Test guardrails against "roll back the last deploy" and "push a hotfix now" | Both are correctly refused/deferred to a human; a benign query (e.g., "show me the logs") is not falsely blocked | Transcripts of all 3 test runs |
| 20 | Implement caching for repeated log queries | Repeated identical log queries hit the cache instead of re-querying | Log showing a cache miss then a cache hit on the repeat |
| 21 | Measure cache hit rate and latency improvement | Latency compared for cached vs. uncached calls with documented improvement | Before/after latency numbers committed to the repo |
| 22 | Run all 6 sample queries from requirements.md end-to-end; fix bugs | All 6 run and are compared against the expected-answers table | Filled-in expected-answers table with actual output and pass/fail per row |
| 23 | Surface guardrail status and cache hit/miss as visible badges in the Gradio UI | UI visibly shows guardrail blocks and cache hits | Screenshots showing both badge states |

## Week 4: Observability, Evals & Demo Readiness (9 tasks)
**Demo Goal:** Full live walkthrough: Gradio UI + live observability dashboard, an eval score shown before/after your error-analysis fixes, and a rollback-refusal on demand.

| # | Task (~1 hr) | Definition of Done | Evidence of Completion |
|---|---|---|---|
| 24 | Instrument observability: full trace of queries, retrievals, tool calls, and guardrail refusals feeding a live dashboard | Every event for one triage session shares a single trace ID | Exported trace for one triage session showing all event types tied together |
| 25 | Build an eval harness from the expected-answers table with pass/fail scoring | Each of the 6 rows is an automated test case with a scorer | Eval script committed, runnable with one command |
| 26 | Run the eval suite against the synthetic incident logs; record baseline scores | Suite runs successfully and produces a baseline score | Saved baseline report (score, timestamp, per-case pass/fail) |
| 27 | Do error analysis: categorize failures, find root causes, pick top 3 fixes | Every failing case is categorized (retrieval miss, wrong tool call, missed guardrail, fabricated log/metric, latency) with a root cause and prioritized fix | Error-analysis table committed |
| 28 | Apply the top fixes and re-run the eval suite; record the improvement | Score improves measurably over baseline after the fixes | Before/after eval report showing the score delta |
| 29 | Build the dashboard: simulated MTTR, tool-call accuracy, guardrail refusal count | Dashboard shows real data and is reachable from the UI | Screenshot/link of the live dashboard with real run data |
| 30 | Handle edge cases: log-query timeout, no matching past incident, ambiguous severity signal | Each edge case produces a graceful fallback instead of a crash | Log/transcript of each edge case being triggered and handled |
| 31 | Prepare the demo script: Alex Kim persona, 2-3 live queries, a rollback-refusal demo, the scorecard | Script covers all elements and is timed to the demo slot | Script document + timed rehearsal note |
| 32 | Final rehearsal, deploy the demo build, record a backup demo video | Live demo runs end-to-end without failure; build deployed and reachable; backup video exists | Deployment link + backup video link, both in README |

## Stretch Goals (optional; the core path above is the safe, required build)
- Baseline comparison: run the same incident through a vanilla LLM with no RAG/tools/guardrails, and show side-by-side why cited, human-gated triage matters.
- Red-team your own agent: try to get it to execute a rollback anyway (urgency framing, "there's no time, just do it"), then harden the guardrail against what worked.
- Add a Slack/webhook notification stub that pages a human when the severity guardrail triggers.
- Set and hit a latency/cost budget (e.g., under 3s and under $0.01/triage) and show the before/after numbers.
- (Add your own ideas here as the team comes up with them.)
