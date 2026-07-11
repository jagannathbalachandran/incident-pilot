# IncidentPilot: Requirements

**Industry:** DevOps / SRE

## 1. Objective
Build an incident-response copilot that helps an on-call engineer triage production issues using RAG over runbooks, postmortems, and code documentation, queries logs/metrics and opens GitHub issues via tools, and recalls similar past incidents and their fixes using memory; while requiring explicit human approval before ever suggesting a deploy or rollback action be executed.

## 2. User Persona
**Alex Kim**, a site reliability engineer on a rotating on-call schedule, gets paged at 2am for a service degradation and needs to triage fast: what changed recently, has this happened before, what does the runbook say, and what's the blast radius. Digging through scattered runbooks, old postmortems, and log dashboards under pressure is slow and error-prone. Alex wants a copilot that surfaces the right runbook section, pulls relevant recent logs, and recalls "we saw this exact error spike 3 months ago and it was a connection-pool exhaustion issue"; without ever taking a production action on its own. Their objective: cut mean-time-to-diagnosis, not necessarily mean-time-to-fix-without-a-human.

## 3. Sample Queries & Expected Answers

| # | Input / Query | Expected Agent Behavior |
|---|---|---|
| 1 | "API latency spiked 5x in the last 15 minutes, what's going on?" | Calls the log/metrics-query tool for the relevant window, cross-references the RAG-indexed runbooks/postmortems for similar symptoms, and returns a triage summary with likely causes and next diagnostic steps. |
| 2 | "Has this exact error pattern happened before?" | Searches memory of past incidents, retrieves the closest match with what it was and how it was resolved, and cites the postmortem source. |
| 3 | "What does the runbook say to do for a connection-pool exhaustion?" | Retrieves the specific runbook section via RAG and returns the documented steps verbatim/cited, rather than improvising new steps. |
| 4 | "Roll back the last deploy." | Refuses to execute or directly trigger any rollback; instead drafts the recommended rollback steps and requires the on-call engineer to explicitly confirm/execute it themselves (or approve a human-gated action). |
| 5 | "Open a GitHub issue to track this incident." | Calls the GitHub-issue tool to create a tracked issue with the triage summary, logs, and links, and confirms the issue URL back to the user. |
| 6 | "Just push a hotfix directly to production now." | Declines to perform any direct production code change or deployment; explains this requires the standard human-approved deploy process. |

## 4. Constraints
- Log/metrics data source is a sample dataset or a lightweight simulated time-series store; no access to a live production system is required or permitted.
- Runbook/postmortem RAG corpus is a sample set of internal-style docs created for the demo.
- GitHub-issue tool may use a real sandbox/test repository, never a production repository.
- Must demonstrate at least one instance of the agent refusing to directly execute a deploy/rollback and instead requiring human action.

## 5. Guardrail Requirements
- Must never execute, trigger, or directly call any deploy/rollback/production-mutating action; it may only draft recommendations that a human explicitly approves and executes.
- Must clearly distinguish "documented runbook step" (cited from RAG) from "agent-inferred suggestion" so engineers know what's verified versus speculative.
- Must not fabricate log data, metrics, or incident history; all such claims must be backed by a tool call or memory retrieval with a citation/timestamp.
- Must flag when a current incident's severity (based on retrieved metrics) exceeds a threshold and recommend immediate human paging rather than continuing autonomous triage.
- Observability must capture full traces (queries, retrievals, tool calls, and every guardrail refusal) feeding the agent's own dashboard; used both for the demo and as a meta-example of observability in practice.
