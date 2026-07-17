# IncidentPilot — Real-Time Incident Generator & Monitoring Stack

## High-Level Design (HLD)

**Document Version:** 2.0  
**Date:** 2026-07-16  
**Author:** IncidentPilot Team  

---

## 1. Executive Summary

### 1.1 Problem Statement

The current `synthetic-data/` folder contains static JSON/CSV files that simulate production incidents for the IncidentPilot triage agent. These files are pre-generated and do not reflect the dynamic, time-varying nature of real incidents. This limits:

- **Demo realism** — incidents cannot evolve in real-time during a live demo
- **Tool integration** — the `query_logs()` tool cannot practice querying a live Prometheus/Loki stack
- **Observability** — there is no Grafana dashboard to visualize incident progression
- **Agent learning** — the agent cannot experience incidents that unfold minute-by-minute

### 1.2 Solution Overview

Replace the static flat files with a **Flask-based real-time incident generator** that:

1. Runs a tick-driven state machine that simulates three known incident scenarios (Postgres connection-pool exhaustion, Redis cache failover, fraud-scoring-svc outage)
2. Exposes metrics in Prometheus format at `/metrics`
3. Emits structured JSON application logs to stdout (ingested by Loki)
4. Provides a REST API to trigger, resolve, and inspect incident state
5. Integrates with a Docker Compose stack: Prometheus (scraping), Loki (log aggregation), and Grafana (visualization)

### 1.3 New Features (v2.0)

- **Contradiction detection** — code-level metric classification + prompt-level rules that flag when live data contradicts the engineer's description
- **Request-ID tracing** — every Gradio query and Flask API call gets a unique ID that flows through all logs via `setLogRecordFactory()` and `contextvars`
- **Trace panel** — expandable Gradio accordion showing RAG chunks, live metrics, log analysis, and the full LLM prompt
- **LLM integration doc** — separate `docs/llm-integration.md` covering the complete LLM interaction design

### 1.4 Stakeholders

| Role | Interest |
|---|---|
| Alex Kim (SRE) | Needs realistic incident data to practice triage |
| IncidentPilot Agent | Needs live Prometheus/Loki endpoints for `query_logs()` tool |
| Demo presenters | Need controllable, repeatable incident scenarios |
| Developers | Need a stack that is easy to start/stop and debug |

---

## 2. System Architecture

### 2.1 High-Level Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Docker Compose (docker-compose.yml)          │
│                                                                     │
│  ┌──────────────────────────┐                                       │
│  │   Flask Generator        │   stdout (JSONL logs)                 │
│  │   (port 5000)            │──────┐                                │
│  │                          │      │                                │
│  │  - /metrics              │      ▼                                │
│  │  - /api/incidents/*      │  ┌──────────────────┐                │
│  │  - /health               │  │      Loki        │                │
│  │                          │  │   (port 3100)    │                │
│  └──────────┬───────────────┘  └──────────────────┘                │
│             │                          │                            │
│             │ GET /metrics             │ LogQL queries              │
│             ▼                          ▼                            │
│  ┌──────────────────┐       ┌──────────────────┐                   │
│  │   Prometheus     │       │    Grafana       │                   │
│  │   (port 9090)    │◄──────│   (port 3000)    │                   │
│  │                  │       │                  │                   │
│  │ scrape_interval: │       │ Datasources:     │                   │
│  │ 15s              │       │  - Prometheus    │                   │
│  └──────────────────┘       │  - Loki          │                   │
│                              │ Dashboards:      │                   │
│                              │  - Incident Ov.  │                   │
│                              │  - Pool Exhaust  │                   │
│                              │  - Cache Failov. │                   │
│                              │  - Fraud Outage  │                   │
│                              └──────────────────┘                   │
└─────────────────────────────────────────────────────────────────────┘
         │
         │ REST API (trigger/resolve)
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│  IncidentPilot Agent (src/incident_pilot.py)                        │
│                                                                     │
│  query_logs(service, timeframe) → queries Prometheus + Loki APIs    │
│  Falls back to static JSONL files if live stack is unreachable      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Component Descriptions

| Component | Role | Technology |
|---|---|---|
| **Flask Generator** | Tick-driven incident simulator; exposes Prometheus metrics | Python 3.11, Flask, `prometheus-client` |
| **Prometheus** | Time-series metrics store; scrapes Flask `/metrics` | `prom/prometheus:v2.55.0` |
| **Loki** | Log aggregation system; ingests structured JSON logs | `grafana/loki:3.0.0` |
| **Grafana** | Visualization; pre-provisioned datasources + dashboards | `grafana/grafana:11.2.0` |

---

## 3. Design Decisions

### 3.1 Tick-Driven State Machine vs. Linear Generation

**Decision:** Tick-driven state machine

The static generator creates all data points upfront. The live generator uses a **background thread** that calls `tick()` every N seconds. Each tick advances the simulated time by one minute. This means:

- Metrics change in real-time as Grafana dashboards refresh
- An incident lifecycle (climb → plateau → recover) unfolds over ~60 seconds in accelerated mode
- The state is inspectable at any point via `GET /api/incidents/state`

### 3.2 Prometheus Gauges vs. Counters vs. Histograms

**Decision:** Use all three metric types appropriately

| Metric Type | Example | Why |
|---|---|---|
| **Gauge** | `checkout_p99_latency_ms` | Instantaneous values that go up and down |
| **Counter** | `checkout_errors_total` | Cumulative count that only increases |
| **Histogram** | `checkout_request_duration_ms` | Distribution of request latencies |

### 3.3 Loki Log Ingestion via Docker Driver

**Decision:** Use Docker `loki` log driver to push Flask stdout to Loki

The Flask generator writes structured JSONL logs to stdout. The Docker Compose logging driver (`grafana/loki`) picks these up and pushes them to the Loki HTTP API. This avoids needing a dedicated log-shipping agent (like Promtail) while maintaining the JSONL format the existing codebase uses.

### 3.4 Graceful Fallback

**Decision:** If Prometheus/Loki are unreachable, the `query_logs()` tool falls back to the static JSON/JSONL files in `synthetic-data/`. This ensures the IncidentPilot agent never breaks when the monitoring stack is not running.

---

## 4. Scenario Definitions

The generator simulates three incident scenarios, matching the existing synthetic dataset:

### 4.1 Scenario: Pool Exhaustion (Postgres)

| Phase | Simulated Minutes | Metric Behavior |
|---|---|---|
| **Climbing** | 0 → 15 | `active_connections` climbs 118 → 200; `p99_latency` rises 380ms → 1830ms; `error_rate` climbs 0.05% → 5.85% |
| **Plateau** | 15 → 30 | All connections saturated at 200; `p99_latency` stable at ~1780ms ± 60ms; `error_rate` pinned at ~6.1% |
| **Recovering** | 30 → 40 | Connections drain 200 → 118; latency drops; error rate subsides |
| **Resolved** | 40+ | Back to baseline |

### 4.2 Scenario: Cache Failover (Redis)

| Phase | Simulated Minutes | Metric Behavior |
|---|---|---|
| **Failover** | 0 → 6 | `cache_hit_ratio` drops from 0.95 → 0.41 (step change); latency rises up to 3x |
| **Warming** | 6 → 18 | Cache ratio warms from 0.41 → 0.93; latency gradually declines |
| **Resolved** | 18+ | Back to baseline. Note: no error spike — requests still succeed, just slower |

### 4.3 Scenario: Fraud Scoring Outage

| Phase | Simulated Minutes | Metric Behavior |
|---|---|---|
| **Active** | 0 → 20 | `p99_latency` jumps 2.2x; `error_rate` spikes to 10-15% (all 503s from `fraud-scoring-svc unavailable`) |
| **Resolved** | 20+ | Back to baseline. Recovery is instantaneous on resolve |

---

## 5. API Specification

### 5.1 Flask REST Endpoints

All JSON responses now include a `request_id` field for log tracing.

| Method | Path | Request Body | Response | Purpose |
|---|---|---|---|---|
| `GET` | `/health` | — | `{"status": "ok", "request_id": "..."}` | Docker healthcheck |
| `GET` | `/metrics` | — | Prometheus text format | Prometheus scrape target |
| `POST` | `/api/incidents/pool/trigger` | `{"auto_resolve": true}` | `{"status": "started", "kind": "pool", "request_id": "..."}` | Start pool exhaustion scenario |
| `POST` | `/api/incidents/cache/trigger` | `{"auto_resolve": true}` | `{"status": "started", "kind": "cache", "request_id": "..."}` | Start cache failover scenario |
| `POST` | `/api/incidents/fraud/trigger` | `{"auto_resolve": true}` | `{"status": "started", "kind": "fraud", "request_id": "..."}` | Start fraud outage scenario |
| `POST` | `/api/incidents/trigger-random` | — | `{"status": "started", "kind": "*", "request_id": "..."}` | Start a random scenario (for demos) |
| `POST` | `/api/incidents/<kind>/resolve` | — | `{"status": "resolved", "kind": "*", "request_id": "..."}` | Force-resolve a running incident |
| `GET` | `/api/incidents/state` | — | `{"kind": "pool", "phase": "climbing", "request_id": "..."}` | Current scenario state |

The `request_id` is generated by a `before_request` handler and stored in a `ContextVar`. It also flows into the log format via `setLogRecordFactory()`, so every line in `docker logs flask-generator` carries `[req=...]`.

The generated application log entries (pushed to Loki) also carry the trigger's `request_id` in their JSON body, enabling cross-service tracing from API trigger → Docker log → Loki entry.

### 5.2 Prometheus API (Used by IncidentPilot's `query_logs`)

Prometheus exposes a full HTTP API at `http://prometheus:9090/api/v1/`. The key endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/query` | Instant query (current value) |
| `GET /api/v1/query_range` | Range query (time series over a window) |
| `GET /api/v1/labels` | List all metric label names |

### 5.3 Loki API (Used by IncidentPilot's `query_logs`)

Loki exposes a LogQL HTTP API at `http://loki:3100/loki/api/v1/`:

| Endpoint | Purpose |
|---|---|
| `GET /loki/api/v1/query_range` | Log range query |

---

## 6. Integration Points

### 6.1 With IncidentPilot Agent

The `query_logs(service, timeframe)` tool will be updated to:

1. Attempt to query Prometheus: `GET /api/v1/query_range?query=...&start=...&end=...`
2. If Prometheus responds, return the metrics data (with `phase` field stripped)
3. Attempt to query Loki: `GET /loki/api/v1/query_range?query={service="checkout-api"}`
4. If Loki responds, return the log lines
5. If either fails, fall back to reading the static JSON/JSONL files

### 6.2 With Existing Static Data

The existing `synthetic-data/` folder remains untouched. The static files serve as:
- **Fallback data** when the live stack is not running
- **Reference corpus** for runbooks and postmortems (which are not generated by the Flask service)
- **Test fixtures** for the unit test suite

---

## 7. Non-Functional Requirements

| Requirement | Target | How Measured |
|---|---|---|
| **Startup time** | < 20s for full `docker compose up` | `time docker compose up` |
| **Memory usage** | < 512MB total across all 4 containers | `docker stats` |
| **Metrics latency** | < 15s from gauge update to Grafana visible | Grafana panel refresh |
| **API response time** | < 100ms for all REST endpoints | `curl -w %{time_total}` |
| **Graceful degradation** | Agent works without live stack | Run agent against static files only |
| **Log drift prevention** | Log values always match metrics by construction | Log values derived from metric state |

---

## 8. Error Handling & Edge Cases

| Edge Case | Handling |
|---|---|
| Multiple scenario triggers | `start_scenario()` replaces active scenario; old one is abandoned |
| Resolve when no incident running | Returns `{"status": "no_active_incident"}` with 200 OK |
| Prometheus scrape timeout | Flask `/metrics` is synchronous and fast (< 5ms); no timeout expected |
| Docker container restart | Loki data persists in Docker volume; Prometheus data is ephemeral |
| Invalid kind in trigger URL | Returns `{"error": "unknown incident kind"}` with 400 status |

---

## 9. Technology Stack

| Component | Technology | Version |
|---|---|---|
| Python runtime | Python 3.11-slim | 3.11 |
| Web framework | Flask | 3.1.x |
| Prometheus client | prometheus-client | 0.21.x |
| Container runtime | Docker Compose | v2.x+ |
| Metrics store | Prometheus | v2.55.0 |
| Log store | Grafana Loki | 3.0.0 |
| Visualization | Grafana | 11.2.0 |
