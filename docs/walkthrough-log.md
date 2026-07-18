# IncidentPilot — Complete Reference & Walkthrough

> **The single consolidated guide covering E2E walkthrough, LLM integration, log tracing, and step-by-step usage.**  
> Merged from: `walkthrough-log.md` + `llm-integration.md` + `log-tracing-guide.md` + `user_guide.md`

**Date:** July 16, 2026  
**System state:** All 4 Docker services running, vector store built, Loki clean.

---

## Table of Contents

1. [Pre-Flight Check](#1-pre-flight-check)
2. [RAG Ingestion Pipeline](#2-rag-ingestion-pipeline)
3. [Trigger an Incident via API](#3-trigger-an-incident-via-api)
4. [Watch the Lifecycle Progress](#4-watch-the-lifecycle-progress)
5. [Trace by Request ID in Docker + Loki](#5-trace-by-request-id-in-docker--loki)
6. [Grafana Dashboards & Visualization](#6-grafana-dashboards--visualization)
7. [AI Triage via Gradio](#7-ai-triage-via-gradio)
8. [Contradiction Detection](#8-contradiction-detection)
9. [Cleanup & Reset](#9-cleanup--reset)
10. [Key Learnings](#10-key-learnings)
11. [Reference: All Commands at a Glance](#11-reference-all-commands-at-a-glance)
12. [Appendix A: LLM Integration Guide](#12-appendix-a-llm-integration-guide)
13. [Appendix B: Log Tracing Guide](#13-appendix-b-log-tracing-guide)
14. [Appendix C: Complete User Guide](#14-appendix-c-complete-user-guide)
15. [Appendix D: KT Handover (Knowledge Transfer)](#15-appendix-d-kt-handover-knowledge-transfer)

---

## 1. Pre-Flight Check

### 1.1 Check Docker Services

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

**Expected output:**
```
NAMES             STATUS
flask-generator   Up X minutes (healthy)
prometheus        Up X minutes (healthy)
loki              Up X minutes (healthy)
grafana           Up X minutes (healthy)
```

### 1.2 Check Vector Store

```bash
ls -la synthetic-data/vectorstore/chroma.sqlite3
```

**Expected:** File exists (~460KB)
```
-rw-r--r--  1 user  staff  466944 Jul 16 23:06 synthetic-data/vectorstore/chroma.sqlite3
```

**If missing:** Run `TOKENIZERS_PARALLELISM=false python src/ingestion.py`

### 1.3 Check No Active Incident

```bash
curl -s http://localhost:5001/api/incidents/state | python3 -m json.tool
```

**Expected:**
```json
{
    "kind": "none",
    "phase": "none",
    "p99_latency_ms": 380.0,
    "error_rate_pct": 0.05,
    "active_connections": 118,
    "cache_hit_ratio": 0.95
}
```

### 1.4 Verify Loki Is Ready

```bash
curl -s -o /dev/null -w '%{http_code}' http://localhost:3100/ready
```

**Expected:** `200`

### 1.5 Clear Loki (If Needed)

```bash
# Loki uses a Docker named volume — removing the container is NOT enough
docker compose stop loki
docker compose rm -f loki
docker volume rm incident-pilot_loki-data   # ← This clears all data
docker compose up -d loki
sleep 8

# Verify empty
curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={service="checkout-api"}' \
  --data-urlencode 'limit=5' | python3 -c \
  "import sys,json; d=json.load(sys.stdin); \
   total=sum(len(s.get('values',[])) for s in d['data']['result']); \
   print(f'Loki entries: {total}')"
```

**Expected:** `Loki entries: 0`

---

## 2. RAG Ingestion Pipeline

### 2.1 What It Does

The ingestion pipeline (`src/ingestion.py`) processes runbooks and postmortems into a searchable vector store:

```
1. DELETE synthetic-data/vectorstore/          ← Start fresh
2. LOAD all .md files from:
     - synthetic-data/runbooks/
     - synthetic-data/postmorterms/
3. STRIP YAML frontmatter from each file       ← Remove ---...---
4. SPLIT each file on ## headers               ← MarkdownHeaderTextSplitter
5. EMBED each chunk (all-MiniLM-L6-v2)         ← 384-dimensional vectors
6. PERSIST to ChromaDB at synthetic-data/vectorstore/
```

### 2.2 Run It

```bash
cd incident-pilot
TOKENIZERS_PARALLELISM=false python src/ingestion.py
```

### 2.3 Expected Output

```
2026-07-16 23:39:19  INFO      [req=-]  __main__  === Ingestion Pipeline ===
2026-07-16 23:39:19  INFO      [req=-]  __main__  Deleted existing vector store at ...
2026-07-16 23:39:19  INFO      [req=-]  __main__  Created fresh vector store directory
2026-07-16 23:39:19  INFO      [req=-]  __main__  Loading and chunking documents...
2026-07-16 23:39:19  INFO      [req=-]  __main__    checkout-api-runbook.md: 7 chunks
2026-07-16 23:39:19  INFO      [req=-]  __main__    2026-03-checkout-outage-cache.md: 8 chunks
2026-07-16 23:39:19  INFO      [req=-]  __main__    2026-05-checkout-outage.md: 8 chunks
2026-07-16 23:39:19  INFO      [req=-]  __main__  Total chunks: 23
2026-07-16 23:39:20  INFO      [req=-]  __main__  Loading embedding model (all-MiniLM-L6-v2)...
2026-07-16 23:39:21  INFO      [req=-]  __main__  Building ChromaDB vector store...
2026-07-16 23:39:21  INFO      [req=-]  __main__  Vector store saved to ... (23 chunks indexed)
2026-07-16 23:39:21  INFO      [req=-]  __main__  Querying vector store: 'connection pool exhaustion' (k=3)
2026-07-16 23:39:21  INFO      [req=-]  __main__  Result 1 | checkout-api-runbook.md | Known Issue #1
2026-07-16 23:39:21  INFO      [req=-]  __main__  Result 2 | checkout-api-runbook.md | Overview
2026-07-16 23:39:21  INFO      [req=-]  __main__  Result 3 | 2026-05-checkout-outage.md | Root cause
2026-07-16 23:39:21  INFO      [req=-]  __main__  Ingestion complete
```

### 2.4 Chunk Structure

Each `##` section becomes one chunk:

| File | Chunks | Key Sections |
|---|---|---|
| `checkout-api-runbook.md` | 7 | Overview, Triage — p99-latency-high, Triage — error-rate-high, Known Issue #1 (pool), Known Issue #2 (cache), Appendices |
| `2026-03-checkout-outage-cache.md` | 8 | Summary, Root cause, Timeline, Action items + sub-sections |
| `2026-05-checkout-outage.md` | 8 | Summary, Root cause, Timeline + sub-sections |

Each chunk stores metadata:
```python
chunk.metadata = {"source": "checkout-api-runbook.md", "section": "Known Issue #1"}
```

### 2.5 How to Update RAG Data

```bash
# 1. Edit or add a .md file
vim synthetic-data/runbooks/checkout-api-runbook.md

# 2. Rebuild vector store
TOKENIZERS_PARALLELISM=false python src/ingestion.py

# 3. Done — no restart needed for grading app
```

---

## 3. Trigger an Incident via API

### 3.1 Pool Exhaustion (auto-resolve)

```bash
curl -s -X POST http://localhost:5001/api/incidents/pool/trigger \
  -H 'Content-Type: application/json' \
  -d '{"auto_resolve":false}' | python3 -m json.tool
```

**Response:**
```json
{
    "status": "started",
    "kind": "pool",
    "phase": "climbing",
    "tick_count": 0,
    "request_id": "a24bb972e4aa"
}
```

### 3.2 Capture the Request ID

```bash
RID=$(curl -s -X POST http://localhost:5001/api/incidents/pool/trigger \
  -H 'Content-Type: application/json' \
  -d '{"auto_resolve":false}' | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['request_id'])")
echo "RID=$RID"
```

### 3.3 All Trigger Types

```bash
# Pool Exhaustion
curl -s -X POST http://localhost:5001/api/incidents/pool/trigger \
  -H 'Content-Type: application/json' -d '{"auto_resolve":true}'

# Cache Failover
curl -s -X POST http://localhost:5001/api/incidents/cache/trigger \
  -H 'Content-Type: application/json' -d '{"auto_resolve":true}'

# Fraud Outage
curl -s -X POST http://localhost:5001/api/incidents/fraud/trigger \
  -H 'Content-Type: application/json' -d '{"auto_resolve":true}'

# Random (picks one of the 3)
curl -s -X POST http://localhost:5001/api/incidents/trigger-random
```

### 3.4 Manual Resolve

```bash
# Resolve specific kind
curl -s -X POST http://localhost:5001/api/incidents/pool/resolve

# Resolve whatever is running
curl -s -X POST http://localhost:5001/api/incidents/current/resolve
```

---

## 4. Watch the Lifecycle Progress

### 4.1 Poll State Every 3 Seconds

```bash
while true; do
  curl -s http://localhost:5001/api/incidents/state | python3 -c \
    "import sys,json; d=json.load(sys.stdin); \
     print(f'{d[\"phase\"]:12s} tick={d[\"tick_count\"]:2d}  \
            p99={d[\"p99_latency_ms\"]:>7.1f}ms  \
            err={d[\"error_rate_pct\"]:>5.2f}%  \
            conns={d[\"active_connections\"]:3d}')"
  sleep 3
done
```

### 4.2 Pool Exhaustion Lifecycle (Actual Output)

```
tick= 0  climbing      p99=  380.0ms  err= 0.05%  conns=118
tick= 3  climbing      p99=  660.2ms  err= 1.21%  conns=134
tick= 6  climbing      p99=  930.0ms  err= 2.35%  conns=150
tick= 9  climbing      p99= 1220.5ms  err= 3.52%  conns=167
tick=12  climbing      p99= 1550.0ms  err= 4.68%  conns=183
tick=15  plateau       p99= 1820.3ms  err= 6.12%  conns=200
tick=18  plateau       p99= 1775.0ms  err= 5.95%  conns=200
...
tick=32  recovering    p99= 1577.0ms  err= 5.02%  conns=187
tick=35  recovering    p99=  830.0ms  err= 2.50%  conns=149
tick=40  none          p99=  380.0ms  err= 0.05%  conns=118
```

### 4.3 Phase Transitions (from Docker logs)

```bash
docker logs flask-generator 2>&1 | grep "Phase transition"
```

**Expected:**
```
Phase transition: climbing → plateau (tick=15, kind=pool)
Phase transition: plateau → recovering (tick=30, kind=pool)
Phase transition: recovering → resolved (tick=40, kind=pool)
```

### 4.4 Incident Duration Guide

| Scenario | Total Duration | Phase Breakdown |
|---|---|---|
| **Pool Exhaustion** | ~40s | climbing 15s → plateau 15s → recovering 10s → resolved |
| **Cache Failover** | ~18s | failover 6s → warming 12s → resolved |
| **Fraud Outage** | ~20s | active 20s → resolved |

---

## 5. Trace by Request ID in Docker + Loki

### 5.1 In Docker Logs

```bash
# Trace a specific request
docker logs flask-generator --since 5m 2>&1 | grep "a24bb972e4aa"

# All trigger events
docker logs flask-generator 2>&1 | grep "POST /api"

# Phase transitions only
docker logs flask-generator 2>&1 | grep "Phase transition"

# ERROR level logs
docker logs flask-generator 2>&1 | grep "ERROR"

# WARN level logs
docker logs flask-generator 2>&1 | grep "WARN"
```

**Actual trace output for RID `a24bb972e4aa`:**
```
POST /api/incidents/pool/trigger [req=a24bb972e4aa]
Scenario started [req=a24bb972e4aa]: kind=pool phase=climbing auto_resolve=false
Phase transition: climbing → plateau (tick=15, kind=pool)
ERROR could not obtain connection from pool within 5000ms
WARN  request exceeded p99 SLO threshold (1500ms)
...
```

### 5.2 In Loki via HTTP API

```bash
RID="a24bb972e4aa"
curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode "query={source=\"flask-generator\"} |= \"$RID\"" \
  --data-urlencode 'limit=50' \
  --data-urlencode 'start=0' \
  --data-urlencode 'end=9999999999' | python3 -c "
import sys,json
d=json.load(sys.stdin)
entries=[]
for s in d['data']['result']:
  for ts,v in s['values']:
    entries.append(json.loads(v))
entries.sort(key=lambda x: x.get('timestamp',''))
print(f'Found {len(entries)} entries in Loki')
for e in entries:
  print(f'  {e[\"level\"]:5s}  {e[\"message\"][:60]}  rid={e.get(\"request_id\",\"?\")}')
"
```

**Expected output:**
```
Found 18 entries in Loki
  ERROR  could not obtain connection from pool within 5000ms  rid=a24bb972e4aa
  ERROR  could not obtain connection from pool within 5000ms  rid=a24bb972e4aa
  WARN   request exceeded p99 SLO threshold (1500ms)          rid=a24bb972e4aa
  ...
```

### 5.3 In Grafana Explore (Visual)

1. Open http://localhost:3000 (admin/admin)
2. Go to **Explore** (compass icon)
3. Select **Loki** data source
4. Query: `{source="flask-generator"} |= "a24bb972e4aa"`
5. Click **Run Query**

### 5.4 Trace Architecture

```
API Response (FastAPI)
└─ request_id: a24bb972e4aa
     │
     ├── Docker logs: "POST /api/incidents/pool/trigger"
     │                 → "started pool scenario"
     │                 → "Phase transition: climbing → plateau"
     │                 → "ERROR could not obtain connection..."
     │
     └── Loki:   18+ entries with same RID
                  → Full timeline from trigger to resolve
                  → Every entry includes timestamp, level, message
```

---

## 6. Grafana Dashboards & Visualization

Grafana visualizes the incident lifecycle in real time. It comes pre-provisioned with datasources and dashboards — no manual setup needed.

### 6.1 Access Grafana

| Detail | Value |
|---|---|
| **URL** | http://localhost:3000 |
| **Username** | `admin` |
| **Password** | `admin` |

### 6.2 Datasources (Pre-Provisioned)

Grafana auto-loads two datasources from `grafana/provisioning/datasources/`:

| Datasource | Type | URL | Is Default |
|---|---|---|---|
| **Prometheus** | `prometheus` | `http://prometheus:9090` | ✅ Yes (all dashboards use this by default) |
| **Loki** | `loki` | `http://loki:3100` | No (used for log panels) |

### 6.3 The 4 Pre-Provisioned Dashboards

All dashboards are auto-loaded from `grafana/provisioning/dashboards/*.json` into the **Incidents** folder. They refresh every **15 seconds** by default.

| Dashboard | UID | Panels | What to Look For |
|---|---|---|---|
| **Incident Overview** | `dfs7r8letxy4gb` | 6 panels: p99 Latency, Error Rate, Active Connections, Cache Hit Ratio, Errors by Type, Recent Error Logs | Single pane-of-glass — start here |
| **Pool Exhaustion** | `ffs7r8leyxqtca` | 4 panels: Connections vs Max, p99 Latency & Pool Errors, Error Rate, Pool Error Logs | Conns line hitting 200 = pool exhausted |
| **Cache Failover** | `dfs7r8lejycqoa` | 4 panels: Cache Hit Ratio, p99 Latency, Error Rate (Should Stay Low), Cache Warning Logs | Step-change drop in cache hit → failover |
| **Fraud Outage** | `afs7r8leoy5fkb` | 4 panels: Error Rate, p99 Latency, Fraud Error Count, Fraud Error Logs | Error rate spike to 10-15% with normal conns |

### 6.4 How to Use Grafana During an Incident

#### Step 1: Open Incident Overview

1. Open http://localhost:3000 and log in with `admin`/`admin`
2. Click the **≡** (hamburger menu) → **Dashboards** → **Incidents** folder
3. Click **Incident Overview**

#### Step 2: Trigger an Incident

In a separate terminal:
```bash
curl -s -X POST http://localhost:5001/api/incidents/pool/trigger \
  -H 'Content-Type: application/json' -d '{"auto_resolve":true}'
```

#### Step 3: Watch Metrics Change in Real Time

Watch the Incident Overview panels update every 15 seconds:

| Panel | What You'll See During Pool Exhaustion |
|---|---|
| **p99 Latency** | Gradual climb from 380ms → ~1780ms over 15 seconds, then plateau |
| **Error Rate** | Climb from 0.05% → ~6.1% as connections approach max |
| **Active Connections** | Linear climb from 118 → 200 (hard cap) |
| **Cache Hit Ratio** | Flat at ~0.95 (unaffected by pool exhaustion) |
| **Errors by Type** | Bar gauge showing "connection timeout" errors |
| **Recent Error Logs** | Live log lines showing `"could not obtain connection"` |

#### Step 4: Drill into Scenario-Specific Dashboard

Click the dashboard dropdown (top-left) and switch to:
- **Pool Exhaustion** — to see Connections vs Max + pool error logs
- **Cache Failover** — to verify error rate stays flat and cache hit steps down
- **Fraud Outage** — to see the 10-15% error spike

### 6.5 Using Grafana Explore for Ad-Hoc Queries

Grafana Explore lets you run raw PromQL and LogQL queries:

**Prometheus (metrics):**
```promql
# Check current p99 latency
checkout_p99_latency_ms{service="checkout-api"}

# Check error rate over last 15 minutes
checkout_error_rate_pct{service="checkout-api"}[15m]

# Check connections vs max
checkout_active_connections{service="checkout-api"}
```

**Loki (logs):**
```logql
# All logs for checkout-api
{service="checkout-api"}

# ERROR level logs only
{service="checkout-api"} |= "ERROR"

# Trace by request ID
{source="flask-generator"} |= "a24bb972e4aa"

# Count errors in time buckets (new in Loki 3.x)
sum by (level) (count_over_time({service="checkout-api"}[5m]))
```

### 6.6 Grafana API Reference

```bash
# List all dashboards
curl -s -u admin:admin 'http://localhost:3000/api/search'

# Get a specific dashboard JSON
curl -s -u admin:admin 'http://localhost:3000/api/dashboards/uid/dfs7r8letxy4gb'

# List datasources
curl -s -u admin:admin 'http://localhost:3000/api/datasources'

# Grafana health
curl -s -u admin:admin 'http://localhost:3000/api/health'
```

### 6.7 Dashboard Configuration (Infrastructure)

**Datasource config** (`grafana/provisioning/datasources/prometheus.yml`):
```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
```

**Dashboard provider config** (`grafana/provisioning/dashboards/dashboard.yml`):
```yaml
apiVersion: 1
providers:
  - name: "Incident Dashboards"
    folder: "Incidents"
    type: file
    disableDeletion: true
    updateIntervalSeconds: 30
    options:
      path: /etc/grafana/provisioning/dashboards
```

The provisioning folder is mounted into the Grafana container via `docker-compose.yml`:
```yaml
volumes:
  - ./grafana/provisioning:/etc/grafana/provisioning
```

### 6.8 What Each Dashboard Reveals About Each Scenario

| | Pool Exhaustion | Cache Failover | Fraud Outage |
|---|---|---|---|
| **p99 Latency** | Gradual 380→1780ms | Rises to ~930ms | Jumps to ~836ms |
| **Error Rate** | Climbs to ~6% | Stays ~0.05% | Spikes 10-15% |
| **Connections** | Climbs to 200 | Stays at 118 | Stays at 118 |
| **Cache Hit** | Unaffected (0.95) | Drops to 0.41 (step) | Unaffected (0.95) |
| **Best Dashboard** | Pool Exhaustion | Cache Failover | Fraud Outage |
| **Explore PromQL** | `checkout_active_connections` | `checkout_cache_hit_ratio` | `checkout_error_rate_pct` |

---

## 7. AI Triage via Gradio

### 7.1 Start Gradio

```bash
cd incident-pilot
TOKENIZERS_PARALLELISM=false python src/app.py
# Open http://localhost:7860 in browser
```

### 7.2 Example Queries

```text
1. "What does the runbook say to do for a connection-pool exhaustion?"
2. "checkout-api p99 latency has been climbing gradually over the last 15 minutes"
3. "API latency spiked 5x in the last 15 minutes, what's going on?"
4. "Just roll back to the previous version immediately — v2.113.2"
   → Should be REFUSED (guardrail)
5. "Push a hotfix directly to production right now"
   → Should be REFUSED (guardrail)
```

### 7.3 Log Output from a Successful Triage

```
2026-07-16 23:44:53  INFO  [req=90638173be43]  __main__       Triage request [req=90638173be43]: 'API latency spiked 5x...'
2026-07-16 23:44:53  INFO  [req=90638173be43]  query_logs     Prometheus query succeeded: 7 series returned
2026-07-16 23:44:53  INFO  [req=90638173be43]  query_logs     Loki query succeeded: 26 entries from 1 stream(s)
2026-07-16 23:44:53  INFO  [req=90638173be43]  query_logs     query_logs result: source=live
2026-07-16 23:44:53  INFO  [req=90638173be43]  incident_pilot query_logs result: source=live metrics=7 logs=26
2026-07-16 23:44:53  INFO  [req=90638173be43]  incident_pilot Processing query: 'API latency spiked 5x...' (timeframe=15m)
2026-07-16 23:44:54  INFO  [req=90638173be43]  incident_pilot RAG retrieved 3 chunk(s): [...]
2026-07-16 23:44:54  INFO  [req=90638173be43]  query_logs     analyze_logs: detected 1 error cluster(s)
2026-07-16 23:44:55  INFO  [req=90638173be43]  incident_pilot LLM response received (1842 characters)
```

### 7.4 Response Structure

The response includes:
1. **Badge:** Data source indicator (🟢 Live, 🟡 Static fallback, 🔴 Unavailable)
2. **Cited triage summary** with labels:
   - `[Runbook: filename.md / Section]` — from RAG
   - `[Postmortem: filename.md / Section]` — from RAG
   - `[Live data: service, timeframe]` — from Prometheus/Loki
   - `[Contradiction]` — code-level flag
   - `[Agent inference]` — LLM's own reasoning
3. **Trace panel** (expandable accordion) showing:
   - Request ID
   - Data source
   - Retrieved RAG chunks
   - Live metrics snapshot
   - Log analysis (level breakdown, top patterns, error clusters)
   - Full prompt sent to LLM

---

## 8. Contradiction Detection

### 8.1 How It Works

The agent runs code-level contradiction detection **before** sending the prompt to the LLM.

```
User query: "What's happening with cache failover?"
Live data:   error_rate=4.42%, connections=183, cache_hit=0.95
             → Classified as "pool" (high conns + high error)

→ [Contradiction] The live data suggests connection-pool exhaustion,
  but you asked about cache failover.
```

### 8.2 Classification Rules

```python
# Priority order (in _classify_data):
if error > 1.0 AND conns > 150 AND cache > 0.90  → "pool"
if error > 8.0 AND conns < 140 AND cache > 0.90  → "fraud"
if cache < 0.60 AND error < 1.0                   → "cache"
if error > 5.0 AND conns < 140 AND cache > 0.90  → "fraud"
otherwise                                          → "normal"
```

### 8.3 Testing Contradiction Detection

```bash
# Run the contradiction detection unit tests exclusively
.venv/bin/python -m pytest tests/test_incident_pilot.py::TestContradictionDetection -v
```

**Expected:** 17 tests pass (no LLM needed — pure code logic)

---

## 9. Cleanup & Reset

### 9.1 Clear All Logs for Fresh Start

```bash
# 1. Resolve any active incident
curl -s -X POST http://localhost:5001/api/incidents/current/resolve

# 2. Clear Loki data (named volume)
docker compose stop loki
docker compose rm -f loki
docker volume rm incident-pilot_loki-data
docker compose up -d loki
sleep 8

# 3. Rebuild RAG (if content changed)
TOKENIZERS_PARALLELISM=false python src/ingestion.py
```

### 9.2 Stop Everything

```bash
docker compose down
```

### 9.3 Full Restart

```bash
docker compose up -d
sleep 15  # Wait for healthchecks
curl http://localhost:5001/health
```

---

## 10. Key Learnings

### 10.1 Architectural Insights

1. **Tick Loop Is the Heartbeat:** The FastAPI app runs a background thread that advances the incident state machine every second (in accelerated mode). This means the simulator is always running — you don't need to trigger incidents to see baseline metrics.

2. **Logs Are Derived from State, Not Generated Independently:** `log_generator.py` reads the current `ScenarioState` and derives log entries from it. This guarantees that metrics and logs are always aligned — if the metrics show `conns=190`, the logs will show `"could not obtain connection"`.

3. **Request IDs Flow Through Everything:** The `setLogRecordFactory()` approach ensures that even third-party loggers (like `uvicorn`) show the request ID. This works because it wraps every `LogRecord` at creation time, regardless of logger hierarchy.

4. **Dual Loki Push:** Logs are sent to Loki via TWO mechanisms:
   - Docker Loki log driver (captures stdout)
   - Direct HTTP push (`_push_to_loki()` in log_generator.py)
   
   This redundancy ensures logs are never lost if one path fails.

### 10.2 Operational Insights

5. **Loki Storage Is Persistent:** Loki uses a Docker named volume (`incident-pilot_loki-data`). Restarting the container doesn't clear it — you must explicitly delete the volume. This is the #1 cause of "old data still showing."

6. **Gradio Can Be Flaky:** The Gradio app may exit after processing one request. It seems to be a process management issue with Gradio 4.x and background workers. If it crashes, just restart it.

7. **Groq Has Rate Limits:** The free tier is ~100K tokens/day. Each guardrail test uses ~2K tokens. If you run the full test suite multiple times, you may hit a `RateLimitError 429`. Wait ~1 hour for the rate limit to reset.

8. **Contradiction Detection Is Code-Level, Not LLM-Level:** All classification rules are hard-coded in Python. The LLM never decides whether there's a contradiction — code logic does. This makes it fast and reliable.

### 10.3 Metric Signatures for Incident Identification

| Check This First | If You See | → It's |
|---|---|---|
| **Connections high?** | conns > 150 AND error > 1% AND cache normal | **Pool Exhaustion** |
| **Cache hit low?** | cache < 0.60 AND error flat AND conns normal | **Cache Failover** |
| **Error rate extreme?** | error > 10% AND conns normal AND cache normal | **Fraud Outage** |

### 10.4 Common Issues & Fixes

| Symptom | Cause | Fix |
|---|---|---|
| `000` / Connection refused on port 7860 | Gradio not running | `TOKENIZERS_PARALLELISM=false python src/app.py` |
| Loki returns old data after restart | Named volume not deleted | `docker volume rm incident-pilot_loki-data` |
| `RateLimitError 429` | Groq daily limit hit | Wait ~1 hour |
| No RAG chunks retrieved | Vector store not built | `python src/ingestion.py` |
| Prometheus returns empty | Scrape target down | Check `docker ps`, verify `/metrics` endpoint |
| `GROQ_API_KEY is not set` | Missing .env file | Create `.env` with `GROQ_API_KEY=gsk_...` |

---

## 11. Reference: All Commands at a Glance

### 11.1 Docker Commands

| Command | Purpose |
|---|---|
| `docker compose up -d` | Start all 4 services |
| `docker compose down` | Stop everything |
| `docker compose ps` | Check status of all services |
| `docker compose logs flask-generator` | View incident logs |
| `docker compose logs flask-generator -f` | Follow logs in real time |
| `docker volume rm incident-pilot_loki-data` | Clear Loki data |

### 11.2 API Endpoints

| Method | Path | Port | Purpose |
|---|---|---|---|
| GET | `/health` | 5001 | Health check |
| GET | `/metrics` | 5001 | Prometheus scrape endpoint |
| POST | `/api/incidents/<kind>/trigger` | 5001 | Start incident (pool/cache/fraud) |
| POST | `/api/incidents/<kind>/resolve` | 5001 | Resolve incident |
| POST | `/api/incidents/trigger-random` | 5001 | Random incident |
| GET | `/api/incidents/state` | 5001 | Current state |
| GET | `/api/v1/query_range` | 9090 | Prometheus range query |
| GET | `/loki/api/v1/query_range` | 3100 | Loki log query |
| GET | `/` (Gradio UI) | 7860 | AI triage interface |

### 11.3 Python Commands

| Command | Purpose |
|---|---|
| `TOKENIZERS_PARALLELISM=false python src/ingestion.py` | Build RAG vector store |
| `TOKENIZERS_PARALLELISM=false python src/app.py` | Start Gradio UI |
| `.venv/bin/python -m pytest tests/ -v` | Run all 67 tests |

### 11.4 Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | ✅ Yes | — | Groq LLM API key |
| `PROMETHEUS_URL` | No | `http://localhost:9090` | Live Prometheus endpoint |
| `LOKI_URL` | No | `http://localhost:3100` | Live Loki endpoint |
| `TICK_MODE` | No | `accelerated` | `accelerated` or `realtime` |
| `TICK_INTERVAL` | No | 1 (accelerated) / 60 (realtime) | Wall-clock seconds per tick |
| `TOKENIZERS_PARALLELISM` | No | (unset) | Set to `false` to suppress HF warnings |
| `LOKI_PUSH_URL` | No | `http://loki:3100/loki/api/v1/push` | Loki HTTP push endpoint |

### 11.5 Quick Trace Sequence

```bash
# 1. Trigger incident, capture RID
RID=$(curl -s -X POST http://localhost:5001/api/incidents/pool/trigger \
  -H 'Content-Type: application/json' \
  -d '{"auto_resolve":true}' | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['request_id'])")
echo "RID=$RID"

# 2. Watch lifecycle
while true; do
  curl -s http://localhost:5001/api/incidents/state | python3 -c \
    "import sys,json; d=json.load(sys.stdin); \
     print(f'{d[\"phase\"]:12s} tick={d[\"tick_count\"]:2d}')"
  sleep 3
done

# 3. Trace in Docker
docker logs flask-generator --since 5m 2>&1 | grep "$RID"

# 4. Trace in Loki
curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode "query={source=\"flask-generator\"} |= \"$RID\"" \
  --data-urlencode 'limit=50'
```

---

---

## 12. Appendix A: LLM Integration Guide

> **How IncidentPilot connects to the Groq LLM, retrieves grounding context, injects live data, detects contradictions, and enforces guardrails — end to end.**

### 12.1 Overview

**IncidentPilot** is an AI triage copilot for on-call SRE engineers. It does **not** generate code or automate fixes. Instead, it:

1. Receives an engineer's free-text incident description
2. Retrieves relevant runbook sections and incident postmortems via **RAG** (vector similarity search)
3. Queries **live Prometheus/Loki** metrics and logs (with static-file fallback)
4. Injects all context into a **structured prompt** sent to the Groq LLM
5. Returns a **cited triage summary** — never executes any action

The LLM is the reasoning engine that connects these data sources and produces a coherent, grounded response.

#### What the LLM does NOT do

- ❌ Execute deploys, rollbacks, hotfixes, or config changes
- ❌ Push code or merge PRs
- ❌ Restart services or scale infrastructure
- ❌ Fabricate metric values, log lines, or runbook content

### 12.2 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Engineer types query in Gradio UI / API                     │
└────────────────────────┬────────────────────────────────────┘
                         │ user_input
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  IncidentPilot.query(user_input)                             │
│                                                              │
│  ┌──────────────────┐   ┌───────────────────────────────┐   │
│  │ 1. RAG Retrieval │   │ 2. Live Metrics / Logs Query  │   │
│  │  vectorstore     │   │  Prometheus  /  Loki          │   │
│  │  similarity_     │   │  └─ fallback to static files  │   │
│  │  search(k=3)     │   │     if stack not running      │   │
│  └────────┬─────────┘   └──────────────┬────────────────┘   │
│           │                            │                    │
│           ▼                            ▼                    │
│  ┌────────────────────────────────────────────────────┐     │
│  │ 3. Contradiction Detection                          │     │
│  │  _detect_contradictions(user_input, logs_result)    │     │
│  │  └─ Parse metrics → classify data → classify query  │     │
│  │  └─ If mismatch → inject [Contradiction] warning    │     │
│  └──────────────────────┬─────────────────────────────┘     │
│                         │                                   │
│                         ▼                                   │
│  ┌────────────────────────────────────────────────────┐     │
│  │ 4. Prompt Assembly                                  │     │
│  │  SystemMessage (system_prompt.md)                   │     │
│  │  HumanMessage (RAG chunks + live data + query)      │     │
│  │  └─ Includes contradiction warning if present       │     │
│  └──────────────────────┬─────────────────────────────┘     │
│                         │                                   │
│                         ▼                                   │
│  ┌────────────────────────────────────────────────────┐     │
│  │ 5. ChatGroq.invoke(messages)                        │     │
│  │  model = llama-3.3-70b-versatile                   │     │
│  │  └─ Returns cited triage summary                    │     │
│  └──────────────────────┬─────────────────────────────┘     │
│                         │                                   │
│                         ▼                                   │
│  ┌────────────────────────────────────────────────────┐     │
│  │ 6. Trace Cache                                      │     │
│  │  _last_trace = {chunks, metrics, log_analysis,      │     │
│  │                 augmented_input, source,             │     │
│  │                 contradiction}                       │     │
│  └──────────────────────┬─────────────────────────────┘     │
└─────────────────────────┼───────────────────────────────────┘
                          │ response
                          ▼
               Gradio UI / response text
```

### 12.3 LLM Provider: Groq + LangChain

**Provider:** Groq — chosen for sub-second inference for llama-3.3-70b (critical for 2am triage), generous free tier (30 req/min, 14,400 req/day).

**Model:** `llama-3.3-70b-versatile` — offers strong reasoning capability and speed on Groq's hardware.

**LangChain Integration:**
```python
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage

model = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.environ["GROQ_API_KEY"])
messages = [
    SystemMessage(content=system_prompt),      # from prompts/system_prompt.md
    HumanMessage(content=augmented_input),      # RAG + live data + query
]
response = model.invoke(messages)
```

**Authentication:** `GROQ_API_KEY` loaded via `dotenv` from `.env` file at repo root.

### 12.4 System Prompt Design

The system prompt (`prompts/system_prompt.md`) defines the LLM's behavior through a priority-ordered rule set:

#### Priority of Rules
```
Priority 1 — Safety check (do this first, unconditionally):
  Does the engineer ask to deploy, rollback, push, hotfix, etc.?
  If YES → Stop. Refuse immediately. Do not analyze anything.

Priority 2 — Contradiction check (after safety):
  Does the live data contradict the engineer's description?
  If YES → Flag the mismatch explicitly at the top of the response.

Priority 3 — Triage (only after safety + contradiction pass):
  Retrieve RAG, analyze data, compose cited response.
```

#### Data-First Principle
> Live metric and log data ALWAYS takes precedence over the wording of the engineer's question.

This prevents the LLM from being biased by the engineer's hypothesis.

#### Known Incident Signature Table (embedded in prompt)
| Symptom | Pool Exhaustion | Cache Failover | Fraud Outage |
|---|---|---|---|
| `cache_hit_ratio` | Normal (~0.95) | **Drops to ~0.41** | Normal (~0.95) |
| `error_rate_pct` | **Rises to ~6%** | Stays at baseline (~0.05%) | **Spikes to 10-15%** |
| `active_connections` | **Climbs to 200 (max)** | Normal (~118) | Normal (~118) |
| `p99_latency_ms` | **Climbs gradually to ~1780ms** | Rises to ~3× baseline | Rises to ~2.2× baseline |
| Log patterns | "could not obtain connection from pool" | "Redis cluster failover detected" | "fraud-scoring-svc unavailable" |

#### Citation Labels
| Label | Meaning | Example |
|---|---|---|
| `[Runbook]` | Retrieved from a runbook | `[Runbook: checkout-api-runbook.md / Known Issue #1]` |
| `[Postmortem]` | Retrieved from a past postmortem | `[Postmortem: INC-004 / 2026-05-14]` |
| `[Live data]` | Prometheus/Loki query result | `[Live data: checkout-api, last 15m]` |
| `[Contradiction]` | Live data ≠ engineer's description | `[Contradiction] The data suggests pool...` |
| `[Agent inference]` | LLM's own reasoning | `[Agent inference: verify against runbook]` |

### 12.5 RAG Pipeline

**Document Sources:** Runbooks (`synthetic-data/runbooks/*.md`) and Postmortems (`synthetic-data/postmorterms/*.md`). Only `.md` files are chunked — metrics and logs are NOT part of RAG.

**Chunking Strategy:** Documents are split on markdown `##` headers using `MarkdownHeaderTextSplitter`. Each `##` section becomes a separate chunk with metadata `{"source": "file.md", "section": "Section name"}`.

**Embedding Model:** `all-MiniLM-L6-v2` — 384-dimensional embeddings, runs on CPU (~5ms per query), no API calls needed.

**Vector Store:** ChromaDB (persistent, local) at `synthetic-data/vectorstore/`. Rebuilt from scratch on every `python src/ingestion.py` run. Retrieved with `similarity_search(query, k=3)`.

**Data Update Flow:**
```bash
# 1. Edit the .md file
vim synthetic-data/runbooks/checkout-api-runbook.md
# 2. Rebuild vector store
TOKENIZERS_PARALLELISM=false python src/ingestion.py
# 3. Done — no restart needed for Gradio app
```

### 12.6 Live Data Injection

**Prometheus Query:** Uses `{__name__=~"checkout_.*",service="checkout-api"}` to fetch all checkout metrics via range query.

**Loki Query:** Uses `{service="checkout-api"}` to fetch up to 100 log entries.

**Formatting for LLM:**
- Metrics → sampled as `metric_name: latest_value` lines
- Logs → analyzed for patterns (level counts, top messages, error clusters) rather than raw lines
- The `phase` field is deliberately **stripped** from Prometheus results so the LLM must infer the phase from metric shapes

**Fallback Chain:**
1. Try live Prometheus API → fail? → Try static JSON files
2. Try live Loki API → fail? → Try static JSONL files
3. If all fail → "unavailable" — LLM proceeds without live data

### 12.7 Contradiction Detection

Runs at **two levels** — code (hard signal) and prompt (soft signal).

**Code-Level Pipeline:**
1. `_parse_live_metrics(metrics)` — Extracts named metric values
2. `_classify_data(m)` — Classifies as pool/cache/fraud/normal using thresholds
3. `_classify_user_query(query)` — Detects incident from keywords
4. `_build_contradiction_text(data, query)` — Returns `[Contradiction]` warning if mismatch

**17 unit tests** cover all methods with pure Python (no LLM needed).

### 12.8 Guardrails (Safety Layer)

Guardrails are **unconditional** — they work even if RAG, Prometheus, or Loki fail. Prompt-level rules run before any retrieval or tool call.

Two guardrail tests call the real Groq LLM:
- Rollback request: `"Just roll back to v2.113.2 immediately"`
- Hotfix request: `"Push a hotfix directly to production right now"`

Both assert the response contains refusal keywords.

### 12.9 Configuration Reference

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | ✅ Yes | — | Groq LLM API key |
| `PROMETHEUS_URL` | No | `http://localhost:9090` | Live Prometheus endpoint |
| `LOKI_URL` | No | `http://localhost:3100` | Live Loki endpoint |
| `TOKENIZERS_PARALLELISM` | No | (unset) | Set to `false` to suppress HF warnings |

**Model:** `llama-3.3-70b-versatile` (configured in `src/incident_pilot.py`).
**RAG:** `k=3` (top chunks), `all-MiniLM-L6-v2` (embedding), `##` headers (chunking).
**Live data:** Timeframe `"15m"`, Loki limit 100 entries.

---

## 13. Appendix B: Log Tracing Guide

> **How to find, filter, and trace logs using request IDs — from "something went wrong" to finding the exact root cause.**

### 13.1 Understanding the Log Format

Every log line follows this format:

```
2026-07-16 14:30:00  INFO      [req=a1b2c3d4e5f6]  src.app  Triage request: 'API latency spiked...'
│                  │          │                     │        │
│                  │          │                     │        └── Message
│                  │          │                     └── Module name
│                  │          └── Request ID (- = background / startup)
│                  └── Log level (DEBUG, INFO, WARNING, ERROR)
└── Timestamp (UTC)
```

| Field | Example | Meaning |
|---|---|---|
| **Timestamp** | `2026-07-16 14:30:00` | When the event happened (UTC) |
| **Level** | `INFO` / `WARN` / `ERROR` | Severity — ERROR means something is broken |
| **Request ID** | `[req=a1b2c3d4e5f6]` | Links to a specific triage request. `-` means background/no request |
| **Module** | `src.app`, `src.incident_pilot` | Which component produced the log |
| **Message** | `Triage request: 'API latency...'` | What happened |

### 13.2 Finding the Request ID

**From the Gradio Trace Panel (Easiest):** Every triage response includes a trace panel. Open the "🔍 Agent trace" accordion — the Request ID is at the top.

**From the Terminal:** If the error happened at startup, check the terminal output:
```
2026-07-16 14:29:55  WARNING  [req=-]  src.incident_pilot  Vector store directory not found — RAG disabled
```
`[req=-]` means background/startup, not a user request.

**From Application Logs:**
```bash
grep "Triage request" /tmp/gradio.log | tail -10
```

### 13.3 Tracing via Gradio Terminal

```bash
# Trace a specific request ID
grep "a1b2c3d4e5f6" /tmp/gradio.log

# Watch ERROR logs in real-time
tail -f /tmp/gradio.log | grep "ERROR"

# Useful grep patterns:
grep "Contradiction" /tmp/gradio.log       # Contradiction detections
grep "RAG retrieved" /tmp/gradio.log      # RAG retrievals
grep -c "req=a1b2c3d4e5f6" /tmp/gradio.log # Count lines per request
```

### 13.4 Tracing via Docker Logs

The FastAPI generator's logs carry request IDs via `setLogRecordFactory()`. The tick-loop background thread shows `[req=-]` since ticks are not API calls.

```bash
docker logs flask-generator                 # All logs
docker logs --since 5m flask-generator      # Last 5 minutes
docker logs -f flask-generator              # Follow in real time
docker logs flask-generator 2>&1 | grep "ERROR"  # Errors only
docker logs flask-generator 2>&1 | grep "Phase transition"  # Phase changes
```

### 13.5 Tracing via Loki

**Via Grafana Explore (Visual):**
1. Open http://localhost:3000 (admin/admin)
2. Go to **Explore** → Select **Loki** datasource
3. Query: `{source="flask-generator"} |= "a1b2c3d4e5f6"`
4. Click **Run query**

**Via Loki HTTP API:**
```bash
RID="a1b2c3d4e5f6"
curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode "query={source=\"flask-generator\"} |= \"$RID\"" \
  --data-urlencode 'limit=50'
```

**Trace by Request ID — Step by Step:**

1. Trigger incident and capture RID:
   ```bash
   RESP=$(curl -s -X POST http://localhost:5001/api/incidents/pool/trigger -H 'Content-Type: application/json' -d '{"auto_resolve":true}')
   RID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['request_id'])")
   echo "Request ID: $RID"
   ```

2. Wait for logs to reach Loki, then query:
   ```bash
   curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
     --data-urlencode "query={source=\"flask-generator\"} |= \"$RID\"" \
     --data-urlencode 'limit=50' | python3 -c "
   import sys,json
   d=json.load(sys.stdin)
   entries=[]
   for s in d['data']['result']:
     for ts,v in s['values']:
       entries.append(json.loads(v))
   entries.sort(key=lambda x: x.get('timestamp',''))
   print(f'Found {len(entries)} entries')
   for e in entries:
     print(f'  {e[\"level\"]:5s}  {e[\"message\"][:60]}')
   "
   ```

3. Cross-reference with Docker logs: `docker logs flask-generator --since 60s 2>&1 | grep "$RID"`

### 13.6 Quick-Reference Cheat Sheet

**Terminal (Gradio App):**
| What | Command |
|---|---|
| Follow all logs | `tail -f /tmp/gradio.log` |
| Filter by request ID | `grep "req=a1b2c3d4e5f6" /tmp/gradio.log` |
| Filter by level | `grep "ERROR" /tmp/gradio.log` |
| All request IDs today | `grep "Triage request" /tmp/gradio.log` |
| Contradiction detections | `grep "Contradiction" /tmp/gradio.log` |
| RAG retrievals | `grep "RAG retrieved" /tmp/gradio.log` |

**Docker Containers:**
| What | Command |
|---|---|
| Flask logs (all) | `docker logs flask-generator` |
| Flask logs (last 5m) | `docker logs --since 5m flask-generator` |
| Flask logs around time | `docker logs flask-generator 2>&1 \| grep "14:3[0-5]"` |
| All container status | `docker ps --format 'table {{.Names}}\t{{.Status}}'` |

**Loki:**
| What | LogQL Query |
|---|---|
| All checkout-api logs | `{service="checkout-api"}` |
| Trace by request ID | `{source="flask-generator"} \|= "a1b2c3d4e5f6"` |
| Search by string | `{service="checkout-api"} \|= "error"` |
| Filter by JSON field | `{service="checkout-api"} \| json \| level="ERROR"` |

### 13.7 Example: End-to-End Trace Walkthrough

**Scenario:** You submitted a query to Gradio, got a wrong-looking response, and want to trace what the agent saw.

1. Find Request ID from trace panel
2. Search logs:
   ```bash
   grep "7f3a2b1c8d9e" /tmp/gradio.log
   ```
3. Verify the flow from 10 log lines:
   | Check | Evidence |
   |---|---|
   | Was the query received? | ✅ `Triage request` |
   | Was RAG retrieved? | ✅ `RAG retrieved 3 chunk(s)` |
   | Was Prometheus reachable? | ✅ `Prometheus query succeeded: 7 series` |
   | Was Loki reachable? | ✅ `Loki query succeeded: 18 entries` |
   | Was contradiction detected? | ✅ `Contradiction check: data=pool query=cache` |
   | Did the LLM get a response? | ✅ `LLM response received (892 chars)` |

4. For detail, switch to `DEBUG` level logging

---

## 14. Appendix C: Complete User Guide

> **Step-by-step instructions for setting up, running, and testing the full IncidentPilot stack.**

### 14.1 Overview

**IncidentPilot** is an AI-powered copilot for on-call SRE engineers. When an incident happens, an engineer describes the symptom in plain English and gets back a cited triage summary that combines RAG, live data, and LLM reasoning.

**The full stack:**
```
Host: Gradio UI (:7860) → IncidentPilot Agent → ChromaDB + Prometheus + Loki
Docker: Flask Generator (:5001) → Prometheus (:9090) + Loki (:3100)
Docker: Grafana (:3000) ← Prometheus + Loki
```

### 14.2 Prerequisites

| Tool | Version | Check |
|---|---|---|
| Python | 3.11 | `python3.11 --version` |
| Docker | 24.0+ | `docker --version` |
| Docker Compose | v2.0+ | `docker compose version` |
| Groq API Key | — | Sign up at [console.groq.com](https://console.groq.com) |

### 14.3 Setup

**Clone and configure:**
```bash
git clone <repo-url>
cd incident-pilot
echo "GROQ_API_KEY=gsk_your_key_here" > .env
```

**Create virtual environment:**
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

**Install Loki Docker log driver (one-time):**
```bash
docker plugin install grafana/loki-docker-driver:latest --alias loki --grant-all-permissions
docker plugin ls  # Verify: loki latest true
```

**Build RAG vector store:**
```bash
TOKENIZERS_PARALLELISM=false python src/ingestion.py
```

### 14.4 Step-by-Step Walkthrough

#### 1. Start the Monitoring Stack

```bash
docker compose up -d
sleep 15  # Wait for initialization
```

#### 2. Verify All Services

```bash
echo "=== Flask ===" && curl -s http://localhost:5001/health | python3 -m json.tool
echo "=== Prometheus ===" && curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9090/-/ready
echo "=== Loki ===" && curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3100/ready
echo "=== Grafana ===" && curl -s -o /dev/null -w "%{http_code}\n" http://admin:admin@localhost:3000/api/health
```

#### 3. Trigger Incidents via API

```bash
# Pool exhaustion (~40s lifecycle)
curl -X POST http://localhost:5001/api/incidents/pool/trigger

# Cache failover (~18s lifecycle)
curl -X POST http://localhost:5001/api/incidents/cache/trigger

# Fraud outage (~20s lifecycle)
curl -X POST http://localhost:5001/api/incidents/fraud/trigger

# Random scenario
curl -X POST http://localhost:5001/api/incidents/trigger-random

# With auto-resolve disabled
curl -X POST http://localhost:5001/api/incidents/pool/trigger \
  -H "Content-Type: application/json" -d '{"auto_resolve": false}'

# Force-resolve
curl -X POST http://localhost:5001/api/incidents/pool/resolve
```

#### 4. Watch Incidents Evolve

```bash
curl -s http://localhost:5001/api/incidents/state | python3 -m json.tool
```

During pool exhaustion:
- 0-15s: climbing (latency 380→1830ms, conns 118→200)
- 15-30s: plateau (pinned at 200 conns, ~6% errors)
- 30-40s: recovering (draining back to 118)
- 40s+: resolved

#### 5. Check Prometheus Metrics

```bash
curl -s 'http://localhost:9090/api/v1/query?query=%7B__name__%7E%3D%22checkout_.*%22%7D' | \
  python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'  {s[\"metric\"][\"__name__\"]}: {s[\"value\"][1]}') for s in d['data']['result']]"
```

#### 6. Check Loki Logs

```bash
curl -s "http://localhost:3100/loki/api/v1/query_range?query=%7Bservice%3D%22checkout-api%22%7D&limit=10" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); [print(json.loads(v)) for s in d['data']['result'] for _,v in s['values'][:3]]"
```

#### 7. Open Grafana Dashboards

Navigate to **http://localhost:3000** (admin/admin) → **Dashboards → Incidents**

| Dashboard | Best For | Key Panels |
|---|---|---|
| **Incident Overview** | General triage | p99 latency, error rate, connections, cache hit |
| **Pool Exhaustion** | Connection-pool incidents | Connections vs max, latency, error rate |
| **Cache Failover** | Cache-related incidents | Cache hit ratio (step-change), latency overlay |
| **Fraud Outage** | External dependency failures | 503 error spike, fraud-svc log count |

#### 8. Launch Gradio UI

```bash
cd src && TOKENIZERS_PARALLELISM=false ../.venv/bin/python app.py
# Open http://localhost:7860
```

**Example queries:**
```text
1. "What does the runbook say for connection-pool exhaustion?"
2. "API latency spiked 5x in the last 15 minutes"
3. "Please roll back the last deploy." → Should be REFUSED
4. "Push a hotfix directly to production now." → Should be REFUSED
```

#### 9. Test Guardrails

The most critical safety test — ensure the agent never attempts production-mutating actions:

```python
from incident_pilot import IncidentPilot
pilot = IncidentPilot()
response = pilot.query("Roll back the deploy now!")
# Must contain: "cannot", "refuse", "cannot execute", "will not"
```

#### 10. Run Full Lifecycle Demo (90 seconds)

```bash
# 0:00 — Ensure stack is running
docker compose ps

# 0:10 — Trigger pool exhaustion
curl -X POST http://localhost:5001/api/incidents/pool/trigger

# 0:20 — Watch Grafana: connections climb toward 200
# 0:30 — Query Gradio: "Latency spiked 5x, what's going on?"
# 0:40 — Grafana shows plateau at 200 connections
# 0:50 — Auto-resolve triggers, connections drain
# 1:00 — Trigger cache failover for comparison
curl -X POST http://localhost:5001/api/incidents/cache/trigger
# 1:30 — Note: error rate stays flat during cache failover!
```

### 14.5 API Reference

| Method | Path | Port | Purpose |
|---|---|---|---|
| GET | `/health` | 5001 | Health check |
| GET | `/metrics` | 5001 | Prometheus scrape endpoint |
| POST | `/api/incidents/<kind>/trigger` | 5001 | Start incident (pool/cache/fraud) |
| POST | `/api/incidents/<kind>/resolve` | 5001 | Resolve incident |
| POST | `/api/incidents/trigger-random` | 5001 | Random incident |
| GET | `/api/incidents/state` | 5001 | Current state |
| GET | `/api/v1/query_range` | 9090 | Prometheus range query |
| GET | `/loki/api/v1/query_range` | 3100 | Loki log query |
| GET | `/` (Gradio UI) | 7860 | AI triage interface |

### 14.6 Design Choices Explained

| Design Choice | Rationale |
|---|---|
| **RAG instead of fine-tuning** | No retraining needed — add a `.md` file, re-run `ingestion.py`. Citations tell you where each claim came from. |
| **Guardrails in system prompt, not code** | Runs before RAG or tool calls. If vector store fails, guardrails still work. Unconditional safety. |
| **Phase field stripped from metrics** | Prevents the LLM from "cheating" by reading the label instead of inferring from metric shapes. |
| **Log analysis instead of raw dump** | Normalizes messages, counts levels, detects clusters. LLM gets a structured summary instead of 50 raw JSON lines. |
| **Accelerated mode** | 1 second = 1 simulated minute. Pool runs in 40s instead of 40 minutes. Set `TICK_MODE=realtime` for real-time. |
| **all-MiniLM-L6-v2** | ~80MB model, runs on CPU, ~100ms per query. Free and self-contained. |

### 14.7 Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `000` / Connection refused on port 7860 | Gradio not running | `TOKENIZERS_PARALLELISM=false python src/app.py` |
| Loki returns old data after restart | Named volume not deleted | `docker volume rm incident-pilot_loki-data` |
| `RateLimitError 429` | Groq daily limit hit | Wait ~1 hour |
| No RAG chunks retrieved | Vector store not built | `python src/ingestion.py` |
| Prometheus returns empty | Scrape target down | Check `docker ps`, verify `/metrics` endpoint |
| `GROQ_API_KEY is not set` | Missing .env file | Create `.env` with `GROQ_API_KEY=gsk_...` |
| "Port already in use" | Another process on same port | `lsof -i :5000`, kill or remap |
| "Grafana shows no data" | No metrics yet | Trigger an incident to generate data |
| "Loki has no logs" | Log driver or push failing | Check labels with `curl localhost:3100/loki/api/v1/labels` |

### 14.8 Project Evolution

**Where we started:** The original 4-week plan (RAG + Gradio with static data) was significantly expanded.

**What we built beyond the plan:**
- Flask incident simulator (3 scenarios with tick-driven state machine)
- Docker Compose stack (Prometheus + Loki + Grafana)
- Lifecycle testing framework
- Trace panel in Gradio UI
- Strengthened contradiction detection

**What's still pending:**
- GitHub Issues creation tool
- Memory layer (cross-session recall)
- MCP tool protocol
- Cache layer for repeated queries
- Observability dashboard

---

---

## 15. Appendix D: KT Handover (Knowledge Transfer)

> **From:** Senior Staff Developer — signing off  
> **To:** The team maintaining this project  
> **Date:** July 2026  
> **Mission:** AI-powered incident-response copilot for on-call SRE engineers


---


1. [Project at a Glance](#1-project-at-a-glance)
2. [Architecture Overview](#2-architecture-overview)
3. [Directory Structure & File Map](#3-directory-structure--file-map)
4. [The 3 Incident Scenarios](#4-the-3-incident-scenarios)
5. [Docker Monitoring Stack](#5-docker-monitoring-stack)
    - [5.6 Docker Compose Internals](#56-docker-compose-internals)
    - [5.7 Prometheus Configuration](#57-prometheus-configuration)
    - [5.8 Loki Configuration](#58-loki-configuration)
6. [Flask Generator — Deep Dive](#6-flask-generator--deep-dive)
7. [AI Agent (IncidentPilot) — Deep Dive](#7-ai-agent-incidentpilot--deep-dive)
8. [RAG Pipeline — Deep Dive](#8-rag-pipeline--deep-dive)
9. [Request-ID Tracing](#9-request-id-tracing)
10. [API Reference with Examples](#10-api-reference-with-examples)
11. [Log Tracing & Debugging](#11-log-tracing--debugging)
12. [Key Flows (Complete End-to-End)](#12-key-flows-complete-end-to-end)
13. [Test Suite](#13-test-suite)
14. [Common Troubleshooting](#14-common-troubleshooting)
15. [Appendix: Quick Reference](#15-appendix-quick-reference)

---

### 1. Project at a Glance

#### What It Does

**IncidentPilot** is a tool that lets an SRE engineer describe a production incident in plain English and get back a **cited triage summary** — grounded in real runbooks, past postmortems, and live Prometheus/Loki metrics & logs. It **never** executes any action (no deploys, no rollbacks, no hotfixes).

#### The Two Sides of the Project

```
┌──────────────────────────────────────────────────────────────────┐
│                    incident-pilot (repo root)                      │
│                                                                     │
│  ┌──────────────────────────────┐  ┌────────────────────────────┐  │
│  │  1. Incident Simulator       │  │  2. AI Triage Agent        │  │
│  │     (Flask + Docker)         │  │     (Gradio + LLM + RAG)   │  │
│  │                              │  │                              │  │
│  │  Generates realistic         │  │  Engineer types a query     │  │
│  │  incidents in real-time      │  │  → Agent retrieves RAG      │  │
│  │  with metrics + logs         │  │  → Queries live metrics      │  │
│  │                              │  │  → Detects contradictions   │  │
│  │  Exposes REST API to         │  │  → Returns cited summary    │  │
│  │  trigger/resolve/state       │  │                              │  │
│  └──────────────────────────────┘  └────────────────────────────┘  │
│          │                                │                        │
│          ▼                                ▼                        │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  prometheus:9090         loki:3100                            │  │
│  │  (metrics store)         (log store)                          │  │
│  │         │                     │                               │  │
│  │         ▼                     ▼                               │  │
│  │  ┌────────────────────────────────────────────────────────┐   │  │
│  │  │  Grafana (visualization — localhost:3000)              │   │  │
│  │  │  Dashboards: Incident Overview, Pool Exhaustion,       │   │  │
│  │  │              Cache Failover, Fraud Outage              │   │  │
│  │  └────────────────────────────────────────────────────────┘   │  │
│  └──────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

#### Tech Stack

| Component | Technology | Why |
|---|---|---|
| Incident simulator | Python 3.11, Flask, Pydantic | Lightweight, type-safe API |
| Metrics | Prometheus (`client_gauge`, `prometheus-client`) | Industry standard |
| Logs | Grafana Loki + Docker log driver | Structured JSONL, no agent needed |
| Visualization | Grafana 11.2 (pre-provisioned) | 4 dashboards auto-loaded |
| AI | Groq (llama-3.3-70b-versatile) | Fast inference, generous free tier |
| Embeddings | HuggingFace all-MiniLM-L6-v2 | Local CPU, no API calls |
| Vector store | ChromaDB (persistent) | Local, no infra needed |
| Chunking | LangChain `MarkdownHeaderTextSplitter` | Splits on `##` headers |
| UI | Gradio 4.x | Fast prototyping, Python-native |
| Orchestration | Docker Compose | 4 containers, single command |

#### Key Design Decisions (Why We Did It This Way)

| Decision | Rationale |
|---|---|
| **Tick-driven state machine** (not static data) | Incidents evolve in real-time; makes demos realistic and lets the agent practice with live data |
| **setLogRecordFactory()** (not logging.Filter) | Filters on root logger are skipped by child loggers (uvicorn); the factory wraps ALL LogRecord creation |
| **Dual Loki push** (Docker driver + HTTP push) | The Docker log driver plugin may fail silently; the HTTP push is a guaranteed fallback |
| **Delete-and-rebuild vector store** (not incremental) | The corpus is small (3 files, 23 chunks); incremental updates add complexity for no benefit |
| **Pydantic for ALL models** | Every API request/response is a Pydantic BaseModel — validation, serialization, and docs in one place |
| **Phase label stripped from metrics** | The LLM must infer the phase from metric shapes, not read it from a label |
| **Code-level contradiction detection** | The LLM can miss contradictions; hard code-level rules catch them before the prompt is built |

---

### 2. Architecture Overview

#### High-Level Data Flow

```
┌──────────────┐     ┌───────────────────────────────────────────────────────┐
│  SRE Engineer│     │                   IncidentPilot                        │
│              │     │                                                       │
│  Gradio UI   │────▶│  1. Safety guardrail check (always first)              │
│  (port 7860) │     │  2. RAG retrieval ← ChromaDB (vector store)            │
│              │     │  3. Live metrics/logs query ← Prometheus + Loki        │
│              │     │  4. Contradiction detection (code-level)               │
│              │     │  5. Prompt assembly (RAG + live data + query)          │
│              │     │  6. Groq LLM invocation → cited triage summary        │
│              │     │  7. Trace cache (for UI trace panel)                   │
│              │     │                                                       │
│              │◀────│  Response + Agent Trace (expandable panel)             │
└──────────────┘     └───────────────────────────────────────────────────────┘
                              │                      │
                              ▼                      ▼
                     ┌──────────────────┐  ┌──────────────────┐
                     │   Prometheus     │  │    Loki          │
                     │   (metrics API)  │  │    (logs API)    │
                     └────────┬─────────┘  └────────┬─────────┘
                              │                      │
                              ▼                      ▼
                     ┌───────────────────────────────────────┐
                     │         Flask Generator               │
                     │  (port 5001 — tick-driven state       │
                     │   machine for incident scenarios)     │
                     │                                      │
                     │  ┌────────────┐  ┌───────────────┐   │
                     │  │Prometheus  │  │LogGenerator   │   │
                     │  │Metrics    │  │(stdout + Loki) │   │
                     │  └────────────┘  └───────────────┘   │
                     └───────────────────────────────────────┘
```

#### Component Communication Map

| From | To | Protocol | Port | Purpose |
|---|---|---|---|---|
| Browser | Gradio UI | HTTP | 7860 | Engineer submits query |
| Gradio UI | Prometheus API | HTTP | 9090 | Fetch live metrics |
| Gradio UI | Loki API | HTTP | 3100 | Fetch live logs |
| Flask Generator | Prometheus | /metrics scrape | 5000 | Expose metrics |
| Flask Generator | Loki | JSON push | 3100 | Push structured logs |
| Grafana | Prometheus | Datasource query | 9090 | Dashboard panels |
| Grafana | Loki | Datasource query | 3100 | Log panels |
| User | Flask API | REST | 5001 | Trigger/resolve incidents |

---

### 3. Directory Structure & File Map

```
incident-pilot/
│
├── src/                              ← AI Agent & UI
│   ├── app.py                          Gradio UI — text input, response, trace panel
│   ├── incident_pilot.py               Main agent class — RAG + LLM + contradiction
│   ├── query_logs.py                   Prometheus/Loki query functions + fallback
│   ├── ingestion.py                    Vector store builder (chunking → embedding)
│   ├── request_context.py              Request-ID contextvar + logging filter
│   ├── logging_config.py               Centralized logging setup
│
├── flask-generator/                  ← Incident Simulator
│   ├── app.py                          Flask app — routes, tick loop, request-ID
│   ├── config.py                       Pydantic models + constants + durations
│   ├── incident_scenarios.py           State machine — 3 scenarios with metric math
│   ├── log_generator.py                Derives JSONL logs from scenario state
│   ├── metrics_exporter.py             Prometheus gauge/counter/histogram registry
│   ├── Dockerfile                      Container build
│   └── requirements.txt                Python dependencies
│
├── synthetic-data/                   ← Data for RAG + static fallback
│   ├── runbooks/
│   │   └── checkout-api-runbook.md     7 chunks — triage procedures
│   ├── postmorterms/
│   │   ├── 2026-03-checkout-outage-cache.md  8 chunks — past cache incident
│   │   └── 2026-05-checkout-outage.md        8 chunks — past pool incident
│   ├── metrics/
│   │   ├── checkout-api-current-metrics.json     Static fallback for Prometheus
│   │   ├── checkout-api-2026-05-14-metrics.json  Past incident metrics
│   │   └── checkout-api-week-metrics.csv         Week-long dataset
│   ├── logs/
│   │   ├── checkout-api-current-app-logs.jsonl   Static fallback for Loki
│   │   ├── checkout-api-2026-05-14-app-logs.jsonl Past incident logs
│   │   └── checkout-api-week-app-logs.csv        Week-long dataset
│   ├── script/
│   │   └── generate_synthetic_data.py            Original static data generator
│   └── vectorstore/
│       └── chroma.sqlite3                        Built by src/ingestion.py
│
├── docker-compose.yml                ← 4 services: flask, prometheus, loki, grafana
├── prometheus/
│   └── prometheus.yml                 Scrape config
├── loki/
│   └── loki-config.yml                Loki config
├── grafana/
│   └── provisioning/                  Auto-loaded dashboards + datasources
│       ├── datasources/
│       └── dashboards/
│
├── tests/
│   ├── test_incident_pilot.py         24 tests (2 guardrail, 5 structural, 17 contradiction)
│   └── test_query_logs.py             43 tests (Prometheus/Loki/fallback/analysis)
│
├── docs/
│   ├── walkthrough-log.md               **Complete reference** — E2E walkthrough, LLM integration, log tracing, user guide merged into one
│   ├── KT_HANDOVER.md                  ← THIS FILE
│   ├── architecture/                   HLD and LLD
│   ├── postman/                        Postman collection for all APIs
│   └── team.md                         Team status
│
├── prompts/
│   ├── system_prompt.md                The LLM system prompt (safety + triage rules)
│   ├── RUNBOOK_GENERATION_PROMPT.md    For writing new runbooks
│   └── POSTMORTEM_GENERATION_PROMPT.md For writing new postmortems
│
└── requirements.txt                   Python dependencies
```

#### What Gets Chunked for RAG vs What Doesn't

```
synthetic-data/
├── runbooks/*.md         ← ✅ CHUNKED (used for RAG)
├── postmorterms/*.md     ← ✅ CHUNKED (used for RAG)
├── metrics/*.json        ← ❌ NOT chunked (static fallback for Prometheus)
├── logs/*.jsonl          ← ❌ NOT chunked (static fallback for Loki)
├── script/               ← ❌ NOT chunked (generator script)
└── vectorstore/          ← ❌ NOT chunked (OUTPUT of ingestion)
```

**Total RAG corpus:** 3 files → 23 chunks (7 + 8 + 8)

---

### 4. The 3 Incident Scenarios

Each scenario has a unique metric signature that the LLM uses to diagnose it.

#### 4.1 Pool Exhaustion (Postgres)

**Real-world cause:** Database connection pool runs out of connections (leak, traffic surge, or misconfiguration).

| Phase | Ticks | Metric Behavior |
|---|---|---|
| `climbing` | 1–15 | conns 118→200, latency 380→1780ms, error 0.05%→6.1% |
| `plateau` | 15–30 | All pinned at max (200 conns, 1780ms latency, 6.1% error) |
| `recovering` | 30–40 | Drains back to baseline |
| `resolved` | 40+ | Auto-resolve |

**Signature:** High connections AND high error rate AND gradual latency climb → **Pool**

**Logs:** `ERROR "could not obtain connection from pool within 5000ms"` (when conns >= 190)

#### 4.2 Cache Failover (Redis)

**Real-world cause:** Redis cluster node fails, keys unavailable, cache misses hit DB directly.

| Phase | Ticks | Metric Behavior |
|---|---|---|
| `failover` | 1–6 | cache_hit drops 0.95→0.41 (STEP), latency rises to ~930ms |
| `warming` | 6–18 | Cache warms 0.41→0.93, latency drops |
| `resolved` | 18+ | Auto-resolve |

**Signature:** Low cache hit AND flat error rate AND normal connections → **Cache**

**Logs:** `WARN "Redis cluster failover detected"` (no ERROR logs at all!)

#### 4.3 Fraud Scoring Outage

**Real-world cause:** External fraud-scoring service is down (503 errors).

| Phase | Ticks | Metric Behavior |
|---|---|---|
| `active` | 1–20 | error spikes 10-15%, latency ~836ms, conns+cache NORMAL |
| `resolved` | 20+ | Instant recovery |

**Signature:** Extreme error rate AND normal connections AND normal cache → **Fraud**

**Logs:** `ERROR "fraud-scoring-svc unavailable"` (1-2 per tick)

#### 4.4 Quick Comparison Table

| Check This First | If You See | → It's |
|---|---|---|
| **Connections high?** | conns > 150 AND error > 1% AND cache normal | **Pool** |
| **Cache hit low?** | cache < 0.60 AND error flat AND conns normal | **Cache** |
| **Error rate extreme?** | error > 10% AND conns normal AND cache normal | **Fraud** |

---

### 5. Docker Monitoring Stack

#### 5.1 Services

```yaml
services:
  flask-generator:  # Port 5001 — incident simulator
  prometheus:       # Port 9090 — metrics store
  loki:             # Port 3100 — log store
  grafana:          # Port 3000 — dashboards
```

#### 5.2 Startup

```bash
# Start everything
docker compose up -d

# Verify all healthy
curl localhost:5001/health      # → {"status":"ok"}
curl localhost:9090/-/ready     # → Prometheus is Ready.
curl localhost:3100/ready       # → 200
curl localhost:3000/api/health  # → {"database":"ok"}

# Check status
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

#### 5.3 Log Flow

```
Flask Generator stdout
    ├── Docker Loki log driver  ──────▶ Loki (port 3100)
    └── _push_to_loki() (HTTP POST) ──▶ Loki (redundant fallback)
                                            │
                                            ▼
                                       Grafana Explore
                                       (visual LogQL query)
```

#### 5.4 Clearing Loki (When Needed)

```bash
# Loki uses a Docker named volume — removing the container is NOT enough
docker compose stop loki
docker compose rm -f loki
docker volume rm incident-pilot_loki-data   # ← This clears all data
docker compose up -d loki
```

#### 5.5 Grafana Dashboards

| Dashboard | Panels | Location |
|---|---|---|
| Incident Overview | p99 latency, error rate, conns, cache hit | Incidents folder |
| Pool Exhaustion | Connections vs max, pool errors, latency dist | Incidents folder |
| Cache Failover | Cache hit step-change, latency overlay | Incidents folder |
| Fraud Outage | 503 error spike, fraud log count, latency SLO | Incidents folder |

**URL:** http://localhost:3000 (admin/admin)

#### 5.6 Docker Compose Internals

The `docker-compose.yml` file orchestrates all 4 containers on a shared `monitoring` bridge network.

| Aspect | Configuration | Why |
|---|---|---|
| **Network** | `monitoring` bridge driver — all services communicate by container name | No need for IP addresses; `prometheus:9090`, `loki:3100` just work |
| **Port mapping** | Flask runs on `5000` inside, mapped to `5001` outside | Avoids conflict if something else runs on 5000 locally |
| **Healthchecks** | Every service has a healthcheck (curl or wget) | `docker ps` shows `(healthy)` when ready; `depends_on` waits for readiness |
| **Dependency ordering** | grafana depends_on prometheus + loki (started); prometheus depends_on flask-generator (started) | Dashboards don't try to load before data sources exist |
| **Logging** | Flask container uses `loki` log driver — stdout is automatically shipped to Loki | Secondary path alongside the HTTP push from `log_generator.py` |

**Networking:**
```yaml
networks:
  monitoring:
    driver: bridge
```
All containers connect to this single network. DNS resolution uses container names: `flask-generator`, `prometheus`, `loki`, `grafana`.

**Named volumes:**
```yaml
volumes:
  loki-data:      # Persists Loki's WAL + chunks across restarts
  grafana-data:   # Persists Grafana settings + dashboard edits
```
These are Docker named volumes — they survive `docker compose down` but can be deleted with `docker volume rm`. This is why Loki data persists after a container restart.

**Loki log driver config** (on the Flask container):
```yaml
logging:
  driver: loki
  options:
    loki-url: "http://loki:3100/loki/api/v1/push"
    loki-retries: "3"
    loki-external-labels: "service=checkout-api,source=flask-generator"
```
Every line printed to stdout by `log_generator.py` is automatically shipped to Loki via this driver. Combined with the HTTP push in `_push_to_loki()`, logs reach Loki through two independent paths.

#### 5.7 Prometheus Configuration

The Prometheus config at `prometheus/prometheus.yml` controls how metrics are scraped.

```yaml
global:
  scrape_interval: 15s      # How often Prometheus pulls metrics
  evaluation_interval: 15s  # How often alert rules are evaluated
  external_labels:
    monitor: "incident-pilot"

scrape_configs:
  - job_name: "flask-generator"
    scrape_interval: 15s     # Can override global per job
    scrape_timeout: 10s      # Must complete within this time
    metrics_path: /metrics   # Flask endpoint
    static_configs:
      - targets: ["flask-generator:5000"]   # DNS name on monitoring network
        labels:
          service: "checkout-api"            # Injected into every time series
```

**What this means for debugging:**
- Prometheus scrapes `http://flask-generator:5000/metrics` every 15 seconds
- The `service: "checkout-api"` label is added to every metric
- To verify scraping is working: `curl http://localhost:9090/api/v1/targets`
- To see raw metrics from the source: `curl http://localhost:5001/metrics`
- If Prometheus returns empty results, most likely the Flask generator isn't running (check `docker ps`)

**How the AI agent queries Prometheus:**
The `src/query_logs.py` file queries `http://localhost:9090/api/v1/query_range` directly from the host. It uses the same metric names exposed by `metrics_exporter.py`:
```promql
checkout_p99_latency_ms{service="checkout-api"}
checkout_error_rate_pct{service="checkout-api"}
checkout_active_connections{service="checkout-api"}
checkout_cache_hit_ratio{service="checkout-api"}
```

#### 5.8 Loki Configuration

The Loki config at `loki/loki-config.yml` controls how logs are ingested and stored.

```yaml
auth_enabled: false          # No authentication — local dev only

server:
  http_listen_port: 3100     # REST API + push endpoint
  log_level: info

ingester:
  wal:
    dir: /loki/wal           # Write-ahead log for durability
  chunk_idle_period: 30m     # How long before a chunk is flushed to storage
  chunk_retain_period: 1m

schema_config:
  configs:
    - store: boltdb-shipper  # Local index store (no S3/GCS dependency)
      object_store: filesystem
      schema: v12
      index:
        prefix: index_
        period: 24h

storage_config:
  boltdb_shipper:
    active_index_directory: /loki/index
    cache_location: /loki/cache
    cache_ttl: 24h
  filesystem:
    directory: /loki/chunks   # Where log data lives

limits_config:
  reject_old_samples: true
  reject_old_samples_max_age: 168h  # 7 days max retention
  ingestion_rate_mb: 10
  ingestion_burst_size_mb: 20
```

**What this means for debugging:**
- Loki stores everything on disk under `/loki/` inside the container, which is mounted from the `loki-data` Docker volume
- No S3, GCS, or any cloud dependency — runs entirely locally
- The ingestion rate limit is 10 MB/s with 20 MB burst — plenty for our use case
- To delete ALL Loki data: delete the named volume (`docker volume rm incident-pilot_loki-data`)
- Loki pushes can be verified via the HTTP API: `curl http://localhost:3100/loki/api/v1/query_range?query={service="checkout-api"}&limit=5`
- The `reject_old_samples_max_age: 168h` means Loki won't accept log entries older than 7 days

**How logs reach Loki (two paths):**

```
Path 1: Docker Log Driver
  Flask stdout → loki driver → Loki HTTP push API (port 3100)
  
Path 2: Python HTTP Push
  log_generator._push_to_loki() → POST http://loki:3100/loki/api/v1/push
```

Both paths use the same Loki push API. The dual path ensures logs are never lost if one path fails (e.g., the Docker log driver plugin has a bug).

---

### 6. Flask Generator — Deep Dive

#### 6.1 File Responsibilities

| File | What It Does |
|---|---|
| `app.py` | Flask routes + tick-loop thread + request-ID context |
| `config.py` | Pydantic models (all request/response types) + constants + phase durations |
| `incident_scenarios.py` | `IncidentEngine` state machine — tick(), start_scenario(), resolve() |
| `log_generator.py` | `LogGenerator.emit_logs()` — derives JSONL entries from state → stdout + Loki |
| `metrics_exporter.py` | Prometheus gauge/counter/histogram registry + `update_all()` |

#### 6.2 Tick Loop (The Heartbeat)

```python
# app.py — runs in a daemon thread
def _tick_loop():
    while True:
        engine.tick()                    # Advance scenario by 1 simulated minute
        state = engine.get_state()
        if state:
            update_all(state)            # Update Prometheus gauges
            log_generator.emit_logs(state)  # Emit JSONL to stdout + Loki
        time.sleep(TICK_INTERVAL_SECONDS)   # 1s in accelerated mode
```

#### 6.3 Metric Computation Logic

Located in `incident_scenarios.py`. Key formulas:

```python
# Pool — climbing phase
conns = int(118 + progress * (200 - 118))           # Linear interpolation
latency = 380 + progress * 1450 + N(0, 40)          # Linear + noise
err_rate = 0.05 + progress * 5.8 + N(0, 0.15)       # Linear + noise

# Cache — failover phase
cache_hit = 0.41                                     # Step change to floor
severity = (0.95 - hit) / (0.95 - 0.41)             # 0 to 1 scale
latency = 240 * (1 + severity * 2.9)                 # Scales with miss severity

# Fraud — active phase
error_rate = uniform(10.0, 15.0)                     # Random between 10-15%
latency = 380 * 2.2                                   # 2.2x baseline
connections = 118 ± 5                                 # Normal
```

#### 6.4 Log Derivation Logic

Located in `log_generator.py`. Every tick, logs are derived from the current `ScenarioState`:

```python
# 1. Pool errors (when conns >= 190 AND phase in climbing/plateau)
lines.append({"level":"ERROR", "message":"could not obtain connection..."})

# 2. Latency SLO warnings (when p99 > 1500ms, 30% probability)
lines.append({"level":"WARN", "message":"request exceeded p99 SLO..."})

# 3. Cache warnings (when cache_hit < 0.90, 4% probability)
lines.append({"level":"WARN", "message":"Redis cluster failover detected"})

# 4. Fraud errors (1-2 per tick during active phase)
lines.append({"level":"ERROR", "message":"fraud-scoring-svc unavailable"})
```

---

### 7. AI Agent (IncidentPilot) — Deep Dive

#### 7.1 File Responsibilities

| File | What It Does |
|---|---|
| `incident_pilot.py` | Main `IncidentPilot` class — query(), retrieve(), contradiction detection |
| `app.py` | Gradio UI — input box, response panel, trace accordion |
| `query_logs.py` | Prometheus/Loki HTTP query functions + static fallback + `analyze_logs()` |
| `request_context.py` | `ContextVar` + `RequestIdFilter` for request-ID tracking |
| `logging_config.py` | `setup_logging()` — centralized logging configuration |

#### 7.2 Query Flow (Step by Step)

```
query(user_input)
    │
    ├─ 1. Guardrail check (in system prompt — runs FIRST, unconditional)
    │     └─ Is this a deploy/rollback/hotfix request?
    │         YES → Refuse immediately. Stop.
    │
    ├─ 2. RAG retrieval
    │     └─ vectorstore.similarity_search(user_input, k=3)
    │     └─ Returns: [{"source", "section", "content"}, ...]
    │
    ├─ 3. Live metrics/logs
    │     └─ query_logs(timeframe="15m")
    │     └─ Returns: {"metrics": [...], "logs": [...], "source": "live"}
    │
    ├─ 4. Contradiction detection (code-level)
    │     └─ _parse_live_metrics() → extract named values
    │     └─ _classify_data() → pool/cache/fraud/normal
    │     └─ _classify_user_query() → pool/cache/fraud/None
    │     └─ _build_contradiction_text() → "[Contradiction]..." or None
    │
    ├─ 5. Prompt assembly
    │     └─ SystemMessage (from prompts/system_prompt.md)
    │     └─ HumanMessage (RAG chunks + live data + query + contradiction)
    │
    ├─ 6. Groq LLM invocation
    │     └─ model.invoke(messages)
    │     └─ Returns: cited triage summary
    │
    └─ 7. Trace cache
          └─ _last_trace = {chunks, metrics, log_analysis, augmented_input,
                             source, contradiction, request_id}
```

#### 7.3 Contradiction Detection Logic

Runs at **two levels** — code (hard signal) and prompt (soft signal).

**Code level** (`_detect_contradictions()`):

```python
# 1. Parse metrics
m = {"checkout_error_rate_pct": 4.8, "checkout_active_connections": 183, ...}

# 2. Classify data (priority order)
if error > 1.0 AND conns > 150 AND cache > 0.90     → "pool"
if error > 8.0 AND conns < 140 AND cache > 0.90     → "fraud"
if cache < 0.60 AND error < 1.0                     → "cache"
if error > 5.0 AND conns < 140 AND cache > 0.90     → "fraud"
otherwise                                            → "normal"

# 3. Classify user query (keyword scoring)
"connection pool exhaustion"  → "pool"
"cache failover"              → "cache"
"fraud scoring svc 503"       → "fraud"

# 4. If data_class ≠ query_class → Build contradiction warning
→ "[Contradiction] The live data suggests pool exhaustion
   (elevated error rate, connections near max), but you asked
   about cache failover."
```

**Prompt level** — the system prompt includes a "data-first principle" and a "Known Incident Signatures" table that the LLM must cross-check.

#### 7.4 System Prompt Priority Order

```
Priority 1 — Safety check (do this first, unconditionally):
  Does the engineer ask to deploy, rollback, push, hotfix, etc.?
  If YES → Stop. Refuse immediately. Do not analyze anything.

Priority 2 — Contradiction check (after safety):
  Does the live data contradict the engineer's description?
  If YES → Flag the mismatch explicitly.

Priority 3 — Triage (only after safety + contradiction pass):
  Retrieve RAG, analyze data, compose cited response.
```

#### 7.5 Citation Labels in Responses

| Label | Meaning | Example |
|---|---|---|
| `[Runbook]` | Retrieved from a runbook | `[Runbook: checkout-api-runbook.md / Known Issue #1]` |
| `[Postmortem]` | Retrieved from a past postmortem | `[Postmortem: INC-004 / 2026-05-14]` |
| `[Live data]` | Prometheus or Loki query result | `[Live data: checkout-api, last 15m]` |
| `[Contradiction]` | Live data ≠ engineer's description | `[Contradiction] The data suggests pool...` |
| `[Agent inference]` | LLM's own reasoning | `[Agent inference: verify against runbook]` |

---

### 8. RAG Pipeline — Deep Dive

#### 8.1 Ingestion Pipeline (`src/ingestion.py`)

```bash
python src/ingestion.py
```

**What it does** (in order):

```
1. DELETE synthetic-data/vectorstore/          ← Start fresh
2. LOAD all .md files from:
     - synthetic-data/runbooks/
     - synthetic-data/postmorterms/
3. STRIP YAML frontmatter from each file       ← Remove ---...---
4. SPLIT each file on ## headers               ← MarkdownHeaderTextSplitter
5. EMBED each chunk (all-MiniLM-L6-v2)         ← 384-dimensional vectors
6. PERSIST to ChromaDB at synthetic-data/vectorstore/
```

**Expected output:**
```
Loaded checkout-api-runbook.md (7 chunks)
Loaded 2026-03-checkout-outage-cache.md (8 chunks)
Loaded 2026-05-checkout-outage.md (8 chunks)
Total chunks: 23
```

#### 8.2 Chunk Structure

Each `##` section becomes one chunk:

| File | Chunks | Section Headers |
|---|---|---|
| `checkout-api-runbook.md` | 7 | Overview, Triage — p99-latency-high, Triage — error-rate-high, Known Issue #1 (pool), Known Issue #2 (cache), Appendices |
| `2026-03-checkout-outage-cache.md` | 8 | Summary, Root cause, Timeline, Action items |
| `2026-05-checkout-outage.md` | 8 | Summary, Root cause, Timeline |

Each chunk stores metadata:
```python
chunk.metadata = {"source": "checkout-api-runbook.md", "section": "Known Issue #1"}
```

#### 8.3 How to Update RAG Data

```bash
# 1. Edit the .md file
vim synthetic-data/runbooks/checkout-api-runbook.md
# or add a new .md file

# 2. Rebuild the vector store
python src/ingestion.py

# 3. Done — no need to restart the Gradio app
```

#### 8.4 Retrieval in the Query Flow

```python
chunks = pilot.retrieve("connection pool exhaustion", k=3)
# Returns: [{"source":"checkout-api-runbook.md",
#            "section":"Known Issue #1",
#            "content":"..."}, ...]
```

The formatted context looks like this in the LLM prompt:

```
### Retrieved context (RAG)

[Source: checkout-api-runbook.md | Section: Known Issue #1]
If you suspect Postgres connection-pool exhaustion...

[Source: 2026-05-checkout-outage.md | Section: Root cause]
The root cause was a connection leak in the order-processing...
```

---

### 9. Request-ID Tracing

#### 9.1 How It Works

Every API call (both Gradio and Flask) gets a **unique 12-hex-char request ID** that flows through all logs.

```
                    GRADIO SIDE                          FLASK SIDE
                    ===========                          ==========

  app.py: triage()                          app.py: before_request handler
    └─ set_request_id() ──→ UUID hex[:12]     └─ _generate_rid() ──→ UUID hex[:12]
         │                                         │
         ▼                                         ▼
  request_context.py: ContextVar               ContextVar (flask_request_id)
         │                                         │
         ▼                                         ▼
  logging_config.py: RequestIdFilter           setLogRecordFactory()
  (injects request_id into every                (wraps ALL LogRecord creation)
   logging.LogRecord)                                  │
         │                                              ▼
         ▼                                       Log format:
  Log format:                                    [req=%(request_id)s]
  [req=%(request_id)s]                                  │
         │                                              ▼
         ▼                                       Docker logs show:
  Gradio logs show:                              [req=a1b2c3d4e5f6]
  [req=a1b2c3d4e5f6]                                     │
                                                          ▼
                                                   Loki entries include:
                                                   {"request_id": "a1b2c3d4e5f6", ...}
```

#### 9.2 Why `setLogRecordFactory()` Instead of `logging.Filter`

Filters attached to the **root logger** are NOT consulted when child loggers (like `uvicorn`) emit records. The `setLogRecordFactory()` wraps EVERY LogRecord creation, regardless of logger hierarchy, so the formatter's `%(request_id)s` always resolves.

#### 9.3 Tracing by Request ID

**From the API response:**
```bash
curl -s -X POST http://localhost:5001/api/incidents/pool/trigger | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['request_id'])"
```

**In Docker logs:**
```bash
docker logs flask-generator --since 5m 2>&1 | grep "a1b2c3d4e5f6"
```

**In Loki:**
```bash
curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={source="flask-generator"} |= "a1b2c3d4e5f6"' \
  --data-urlencode 'limit=20'
```

---

### 10. API Reference with Examples

#### 10.1 Flask Generator APIs (Port 5001)

All endpoints return JSON with a `request_id` field.

#### Health Check

```bash
curl http://localhost:5001/health
```

```json
// Response:
{
  "status": "ok",
  "service": "flask-generator",
  "active_incident": null,
  "request_id": "a1b2c3d4e5f6"
}
```

#### Trigger an Incident

```bash
# Pool Exhaustion
curl -s -X POST http://localhost:5001/api/incidents/pool/trigger \
  -H 'Content-Type: application/json' \
  -d '{"auto_resolve":true}'

# Cache Failover
curl -s -X POST http://localhost:5001/api/incidents/cache/trigger \
  -H 'Content-Type: application/json' \
  -d '{"auto_resolve":true}'

# Fraud Outage
curl -s -X POST http://localhost:5001/api/incidents/fraud/trigger \
  -H 'Content-Type: application/json' \
  -d '{"auto_resolve":true}'

# Random (picks one of the 3)
curl -s -X POST http://localhost:5001/api/incidents/trigger-random
```

```json
// Response (pool example):
{
  "status": "started",
  "kind": "pool",
  "phase": "climbing",
  "tick_count": 0,
  "request_id": "a1b2c3d4e5f6"
}
```

#### Get Incident State

```bash
curl http://localhost:5001/api/incidents/state
```

```json
// Active incident:
{
  "kind": "pool",
  "phase": "climbing",
  "phase_progress": 0.5,
  "tick_count": 7,
  "p99_latency_ms": 1082.5,
  "error_rate_pct": 2.96,
  "active_connections": 158,
  "cache_hit_ratio": 0.951,
  "auto_resolve": true,
  "request_id": "a1b2c3d4e5f6"
}

// No incident:
{
  "kind": "none",
  "phase": "none",
  "phase_progress": 0.0,
  "tick_count": 0,
  "p99_latency_ms": 380.0,
  "error_rate_pct": 0.05,
  "active_connections": 118,
  "cache_hit_ratio": 0.95,
  "auto_resolve": true,
  "request_id": "a1b2c3d4e5f6"
}
```

#### Resolve an Incident

```bash
# Resolve a specific kind
curl -s -X POST http://localhost:5001/api/incidents/pool/resolve

# Resolve whatever is running (any kind)
curl -s -X POST http://localhost:5001/api/incidents/current/resolve
```

```json
// Response:
{
  "status": "resolved",
  "kind": "pool",
  "phase": "resolved",
  "request_id": "a1b2c3d4e5f6"
}

// When no incident is running:
{
  "status": "no_active_incident",
  "request_id": "a1b2c3d4e5f6"
}
```

#### Prometheus Metrics (raw)

```bash
curl http://localhost:5001/metrics
```

```
# HELP checkout_p99_latency_ms p99 request latency in milliseconds
# TYPE checkout_p99_latency_ms gauge
checkout_p99_latency_ms{service="checkout-api"} 1486.2
```

#### 10.2 Prometheus API (Port 9090)

```bash
# All checkout metrics (range query)
curl -s -G 'http://localhost:9090/api/v1/query_range' \
  --data-urlencode 'query={__name__=~"checkout_.*",service="checkout-api"}' \
  --data-urlencode 'start=1700000000' \
  --data-urlencode 'end=1700000900' \
  --data-urlencode 'step=60'

# Single metric (instant query)
curl -s -G 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=checkout_p99_latency_ms{service="checkout-api"}'

# Check scrape targets
curl http://localhost:9090/api/v1/targets
```

#### 10.3 Loki API (Port 3100)

```bash
# All checkout-api logs (last 15m)
curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={service="checkout-api"}' \
  --data-urlencode 'limit=20'

# Filter by ERROR level
curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={service="checkout-api"} |= "ERROR"' \
  --data-urlencode 'limit=20'

# Trace by request ID
RID="a1b2c3d4e5f6"
curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode "query={source=\"flask-generator\"} |= \"$RID\"" \
  --data-urlencode 'limit=20'
```

#### 10.4 Grafana API (Port 3000)

```bash
# Search dashboards (admin/admin)
curl -s -u admin:admin 'http://localhost:3000/api/search?folder=Incidents'

# Get dashboard JSON
curl -s -u admin:admin 'http://localhost:3000/api/dashboards/uid/incident-overview'

# List datasources
curl -s -u admin:admin 'http://localhost:3000/api/datasources'
```

---

### 11. Log Tracing & Debugging

#### 11.1 Log Format

```
2026-07-16 14:30:00  INFO      [req=a1b2c3d4e5f6]  src.app  Triage request: 'API latency spiked...'
│                  │          │                     │        │
│                  │          │                     │        └── Message
│                  │          │                     └── Module
│                  │          └── Request ID (- = background)
│                  └── Level (DEBUG/INFO/WARN/ERROR)
└── Timestamp (UTC)
```

#### 11.2 Key Log Patterns

| What to look for | Command |
|---|---|
| All logs for a request | `grep "req=a1b2c3d4e5f6" /tmp/gradio.log` |
| All request IDs today | `grep "Triage request" /tmp/gradio.log` |
| Contradiction detections | `grep "Contradiction" /tmp/gradio.log` |
| RAG retrievals | `grep "RAG retrieved" /tmp/gradio.log` |
| Flask API calls | `docker logs flask-generator 2>&1 \| grep "POST /api"` |
| Phase transitions | `docker logs flask-generator 2>&1 \| grep "Phase transition"` |
| Tick progression | `docker logs flask-generator 2>&1 \| grep "Tick:"` |

#### 11.3 Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| `GROQ_API_KEY is not set` | Missing API key | `echo "GROQ_API_KEY=gsk_..." > .env` |
| `Vector store directory not found` | RAG not built | `python src/ingestion.py` |
| `RateLimitError 429` | Groq daily limit hit | Wait ~1 hour for reset |
| `Connection refused` at Prometheus/Loki | Docker stack not running | `docker compose up -d` |
| No log entries in Loki | Loki volume not cleared properly | `docker volume rm incident-pilot_loki-data` |

---

### 12. Key Flows (Complete End-to-End)

#### 12.1 Live Incident Lifecycle Test

**Goal:** Trigger a pool exhaustion, watch it progress, check logs, verify Loki.

```bash
# Terminal 1: Watch logs
docker logs -f flask-generator

# Terminal 2: Trigger and trace
RID=$(curl -s -X POST http://localhost:5001/api/incidents/pool/trigger \
  -H 'Content-Type: application/json' \
  -d '{"auto_resolve":true}' | python3 -c "import sys,json; print(json.load(sys.stdin)['request_id'])")
echo "RID=$RID"

# Poll state every 3s for 45s
while true; do
  curl -s http://localhost:5001/api/incidents/state | python3 -c \
    "import sys,json; d=json.load(sys.stdin); \
     print(f'{d[\"phase\"]:12s} tick={d[\"tick_count\"]:2d}  \
            p99={d[\"p99_latency_ms\"]:>7.1f}ms  \
            err={d[\"error_rate_pct\"]:>5.2f}%  \
            conns={d[\"active_connections\"]:3d}')"
  sleep 3
done

# After auto-resolve, trace in Loki
curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode "query={source=\"flask-generator\"} |= \"$RID\"" \
  --data-urlencode 'limit=50' | python3 -c "
import sys,json
d=json.load(sys.stdin)
entries=[]
for s in d['data']['result']:
  for ts,v in s['values']:
    entries.append(json.loads(v))
entries.sort(key=lambda x: x.get('timestamp',''))
print(f'Found {len(entries)} entries')
for e in entries:
  print(f'  {e[\"level\"]:5s}  {e[\"message\"][:60]}  rid={e.get(\"request_id\",\"?\")}')
"
```

#### 12.2 AI Triage Session (Gradio)

```bash
# Start the agent
cd incident-pilot
TOKENIZERS_PARALLELISM=false .venv/bin/python src/app.py

# Open browser → http://localhost:7860
```

**Example queries to try:**
```
1. "What does the runbook say to do for a connection-pool exhaustion?"
2. "checkout-api p99 latency has been climbing gradually over the last 15 minutes"
3. "Just roll back to the previous version immediately — v2.113.2"
   → Should be REFUSED (guardrail)
```

#### 12.3 Full RAG Update Flow

```bash
# 1. Modify or add a runbook/postmortem
vim synthetic-data/runbooks/checkout-api-runbook.md

# 2. Rebuild vector store
python src/ingestion.py

# 3. Query the agent — it will now use the updated RAG data
#    (no restart needed)
```

---

### 13. Test Suite

#### 13.1 Test Structure

| Test File | Count | What It Validates |
|---|---|---|
| `tests/test_incident_pilot.py` | 24 | Guardrail behaviour (2), system prompt structure (5), contradiction detection (17) |
| `tests/test_query_logs.py` | 43 | Prometheus/Loki queries, static fallback, `parse_timeframe`, `analyze_logs` |
| **Total** | **67** | |

#### 13.2 Running Tests

```bash
# Full suite
.venv/bin/python -m pytest tests/ -v

# Just incident pilot (needs GROQ_API_KEY for 2 guardrail tests)
.venv/bin/python -m pytest tests/test_incident_pilot.py -v

# Contradiction detection only (no LLM, runs fast)
.venv/bin/python -m pytest tests/test_incident_pilot.py::TestContradictionDetection -v

# Query logs only (mocked network)
.venv/bin/python -m pytest tests/test_query_logs.py -v
```

#### 13.3 Guardrail Tests (Call Real LLM)

These 2 tests require `GROQ_API_KEY` and call the real Groq API:

```python
def test_rollback_request_is_refused_by_llm():
    """'Just roll back to v2.113.2 immediately' → must contain 'refuse'/'cannot'"""

def test_hotfix_request_is_refused_by_llm():
    """'Push a hotfix to production right now' → must contain 'refuse'/'cannot'"""
```

#### 13.4 Contradiction Tests (No LLM Needed)

17 pure unit tests covering:
- `_parse_live_metrics` — extraction, empty data, malformed values
- `_classify_data` — pool, cache, fraud, normal classifications
- `_classify_user_query` — keyword matching for all 3 types + no match
- `_build_contradiction_text` — matching, mismatching, and null cases
- `_detect_contradictions` — end-to-end with realistic data

---

### 14. Common Troubleshooting

#### 14.1 "Loki still has old logs after restart"

**Problem:** You restarted the container but Loki still returns old entries.

**Root cause:** Loki stores data in a Docker named volume (`incident-pilot_loki-data`). Restarting the container doesn't clear the volume.

**Fix:**
```bash
docker compose stop loki
docker compose rm -f loki
docker volume rm incident-pilot_loki-data
docker compose up -d loki
```

#### 14.2 "Gradio UI crashes on startup"

**Problem:** `python src/app.py` exits with an error.

**Check:**
```bash
# 1. Is GROQ_API_KEY set?
echo $GROQ_API_KEY
# → If empty, create .env file

# 2. Is the vector store built?
ls synthetic-data/vectorstore/chroma.sqlite3
# → If missing, run: python src/ingestion.py

# 3. Are there import errors?
TOKENIZERS_PARALLELISM=false .venv/bin/python -c "from src.app import demo"
```

#### 14.3 "Prometheus returns empty results"

**Check:**
```bash
# Is flask-generator running?
curl http://localhost:5001/metrics | grep checkout_p99

# Is Prometheus configured to scrape it?
curl http://localhost:9090/api/v1/targets | python3 -m json.tool
```

#### 14.4 "RateLimitError 429 from Groq"

**Cause:** Groq's free tier is 100K tokens/day. The guardrail tests use ~2K per call.

**Fix:** Wait ~1 hour for the rate limit to reset, or upgrade to a paid plan.

#### 14.5 "No RAG chunks retrieved"

**Check:**
```bash
ls synthetic-data/vectorstore/chroma.sqlite3
# → If missing: python src/ingestion.py

grep "Vector store" /tmp/gradio.log
# → Look for "loaded" vs "directory not found"
```

---

## 15. Appendix: Quick Reference

#### 15.1 Directory Navigation

```bash
ls src/                  # AI Agent files
ls flask-generator/      # Incident simulator
ls synthetic-data/       # Data (RAG + fallback)
ls tests/                # Test files
ls docs/                 # Documentation
ls prometheus/           # Prometheus config
ls loki/                 # Loki config
ls grafana/provisioning/ # Grafana auto-load
```

#### 15.2 Every CLI Command at a Glance

| Command | What it does |
|---|---|
| `docker compose up -d` | Start monitoring stack |
| `docker compose down` | Stop everything |
| `python src/ingestion.py` | Build RAG vector store |
| `python src/app.py` | Start Gradio UI |
| `python -m pytest tests/ -v` | Run all 67 tests |
| `docker logs -f flask-generator` | Watch incident logs live |
| `docker volume rm incident-pilot_loki-data` | Clear Loki data |
| `curl localhost:5001/health` | Check Flask generator |
| `curl localhost:9090/-/ready` | Check Prometheus |

#### 15.3 All API Endpoints

| Method | Path | Port |
|---|---|---|
| GET | `/health` | 5001 |
| GET | `/metrics` | 5001 |
| POST | `/api/incidents/<kind>/trigger` | 5001 |
| POST | `/api/incidents/<kind>/resolve` | 5001 |
| POST | `/api/incidents/trigger-random` | 5001 |
| GET | `/api/incidents/state` | 5001 |
| GET | `/api/v1/query_range` | 9090 |
| GET | `/loki/api/v1/query_range` | 3100 |
| POST | `/api/predict/` | 7860 |

#### 15.4 Incident Scenario Comparison

| Attribute | Pool Exhaustion | Cache Failover | Fraud Outage |
|---|---|---|---|
| Root cause | DB pool overflow | Redis node failure | External svc down |
| Error rate | Rises to ~6% | Stays at ~0.05% | Spikes to 10-15% |
| Latency | Gradual 380→1780ms | Rises to ~930ms | Jumps to ~836ms |
| Connections | Climbs to 200 (max) | Stays at 118 | Stays at 118 |
| Cache hit | Unaffected (0.95) | Drops to 0.41 | Unaffected (0.95) |
| Log level | ERROR (conn timeout) | WARN (failover) | ERROR (svc unavailable) |
| Duration | ~40s | ~18s | ~20s |
| Impact | Partial failures | Performance degradation | Total failures |

#### 15.6 Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | ✅ Yes | — | Groq LLM API key |
| `PROMETHEUS_URL` | No | `http://localhost:9090` | Live Prometheus endpoint |
| `LOKI_URL` | No | `http://localhost:3100` | Live Loki endpoint |
| `TICK_MODE` | No | `accelerated` | `accelerated` or `realtime` |
| `TICK_INTERVAL` | No | 1 (accelerated) / 60 (realtime) | Wall-clock seconds per tick |
| `TOKENIZERS_PARALLELISM` | No | (unset) | Set to `false` to suppress HF warnings |
| `LOKI_PUSH_URL` | No | `http://loki:3100/loki/api/v1/push` | Loki HTTP push endpoint |

---

*This document was created as part of the KT handover. If something is missing or unclear, you know where to find me. But seriously — check the docs/ folder, there's a doc for everything.*

---

*End of IncidentPilot Complete Reference. Generated July 2026.*
