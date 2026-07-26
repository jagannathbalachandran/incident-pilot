# Incident Lifecycle Guide — Trigger → Loki → Grafana

> **A focused, step-by-step guide to generating each incident scenario and tracing it through Loki logs and Grafana dashboards.**  
> System state: Docker stack running, no active incident.  
> Ports: FastAPI=5001, Prometheus=9090, Loki=3100, Grafana=3000, Gradio=7860.

---

## Table of Contents

1. [Before You Start](#1-before-you-start)
2. [Complete API Walkthrough (Step by Step)](#2-complete-api-walkthrough-step-by-step)
3. [Scenario A: Pool Exhaustion](#3-scenario-a-pool-exhaustion)
4. [Scenario B: Cache Failover](#4-scenario-b-cache-failover)
5. [Scenario C: Fraud Scoring Outage](#5-scenario-c-fraud-scoring-outage)
6. [Quick Reference Tables](#6-quick-reference-tables)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. Before You Start

### 1.1 Verify Stack Is Running

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

**Expected output:** All 4 services `(healthy)`:
```
flask-generator   Up X minutes (healthy)
prometheus        Up X minutes (healthy)
loki              Up X minutes (healthy)
grafana           Up X minutes (healthy)
```

### 1.2 Verify No Active Incident

```bash
curl -s http://localhost:5001/api/incidents/state | python3 -m json.tool
```

**Expected:** `"kind": "none"` with baseline metrics:
```json
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
    "request_id": "bdd0f322815a"
}
```

### 1.3 Clear Loki (Optional — Start Fresh)

If Loki has stale data from previous runs:

```bash
docker compose stop loki
docker compose rm -f loki
docker volume rm incident-pilot_loki-data
docker compose up -d loki
sleep 8
echo "Loki ready: $(curl -s -o /dev/null -w '%{http_code}' http://localhost:3100/ready)"
```

---

## 2. Complete API Walkthrough (Step by Step)

This section walks through **every API endpoint** in the stack — FastAPI generator, Prometheus, Loki, and Grafana. Each endpoint includes the exact curl command, request format, expected response, and a real example.

> **Port map:** FastAPI=5001, Prometheus=9090, Loki=3100, Grafana=3000

---

### 2.1 FastAPI Generator API (Port 5001)

All FastAPI responses include a `request_id` field (12 hex chars) for cross-service log tracing.

---

#### API #1: Health Check

**`GET /health`** — Quick health check for the incident simulator.

**Usage:**
```bash
curl -s http://localhost:5001/health | python3 -m json.tool
```

**Response format:**
```json
{
    "status": "ok",
    "service": "flask-generator",
    "active_incident": null,
    "request_id": "a1b2c3d4e5f6"
}
```

| Field | Type | Meaning |
|---|---|---|
| `status` | string | `"ok"` if running |
| `service` | string | Always `"flask-generator"` |
| `active_incident` | string or null | `"pool"` / `"cache"` / `"fraud"` or `null` |
| `request_id` | string | Unique ID for this API call |

**Use this to:** Verify the service is running and see if an incident is active.

---

#### API #2: Prometheus Metrics (raw)

**`GET /metrics`** — Prometheus-compatible metrics endpoint, scraped by Prometheus every 15s.

**Usage:**
```bash
curl -s http://localhost:5001/metrics | grep -E "^# HELP|^checkout"
```

**Response format (plain text, not JSON):**
```
# HELP checkout_p99_latency_ms p99 request latency in milliseconds
# TYPE checkout_p99_latency_ms gauge
checkout_p99_latency_ms{service="checkout-api"} 380.0
# HELP checkout_error_rate_pct request error rate in percent
# TYPE checkout_error_rate_pct gauge
checkout_error_rate_pct{service="checkout-api"} 0.05
# HELP checkout_active_connections current active database connections
# TYPE checkout_active_connections gauge
checkout_active_connections{service="checkout-api"} 118
# HELP checkout_cache_hit_ratio cache hit ratio (0-1)
# TYPE checkout_cache_hit_ratio gauge
checkout_cache_hit_ratio{service="checkout-api"} 0.95
```

**Available metrics:**

| Metric Name | Type | Description |
|---|---|---|
| `checkout_p99_latency_ms` | Gauge | p99 request latency in ms |
| `checkout_error_rate_pct` | Gauge | Error rate in percent |
| `checkout_active_connections` | Gauge | Active DB connections |
| `checkout_cache_hit_ratio` | Gauge | Cache hit ratio (0-1) |

**Use this to:** Verify metrics are being generated without going through Prometheus.

---

#### API #3: Trigger an Incident

**`POST /api/incidents/<kind>/trigger`** — Start an incident scenario.

**Parameters:**
- `<kind>` — One of `pool`, `cache`, `fraud` (case-insensitive)

**Request body (JSON, optional):**
```json
{
    "auto_resolve": true
}
```

| Field | Type | Default | Meaning |
|---|---|---|---|
| `auto_resolve` | boolean | `true` | Auto-resolve after lifecycle completes. Set to `false` to keep the incident running indefinitely |

**Usage:**
```bash
# With auto-resolve (default)
curl -s -X POST http://localhost:5001/api/incidents/pool/trigger \
  -H 'Content-Type: application/json' \
  -d '{"auto_resolve":true}' | python3 -m json.tool

# Without auto-resolve (manual resolve needed)
curl -s -X POST http://localhost:5001/api/incidents/cache/trigger \
  -H 'Content-Type: application/json' \
  -d '{"auto_resolve":false}' | python3 -m json.tool

# Minimal (no body — auto_resolve defaults to true)
curl -s -X POST http://localhost:5001/api/incidents/fraud/trigger | python3 -m json.tool
```

**Response format (pool example):**
```json
{
    "status": "started",
    "kind": "pool",
    "phase": "climbing",
    "tick_count": 0,
    "request_id": "a24bb972e4aa"
}
```

| Field | Type | Meaning |
|---|---|---|
| `status` | string | `"started"` on success |
| `kind` | string | The scenario kind: `pool` / `cache` / `fraud` |
| `phase` | string | Initial phase: `climbing` (pool), `failover` (cache), `active` (fraud) |
| `tick_count` | int | Always 0 at trigger |
| `request_id` | string | **Save this** — used to trace logs in Loki |

**Error response (invalid kind):**
```json
{
    "error": "unknown incident kind 'load'. Valid: ['cache', 'fraud', 'pool']",
    "request_id": "b5c6d7e8f9a0"
}
```

**Step-by-step:**
1. Save the `request_id` from the response — `export RID=a24bb972e4aa`
2. Check the incident started: `curl -s http://localhost:5001/api/incidents/state`
3. Watch the lifecycle (see sections 4-6)

---

#### API #4: Get Current State

**`GET /api/incidents/state`** — Returns current incident metrics and phase.

**Usage:**
```bash
curl -s http://localhost:5001/api/incidents/state | python3 -m json.tool
```

**Response format (active pool incident):**
```json
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
```

**Response format (no incident):**
```json
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
    "request_id": "b5c6d7e8f9a0"
}
```

| Field | Type | Range | Meaning |
|---|---|---|---|
| `kind` | string | `pool` / `cache` / `fraud` / `none` | Which scenario is active |
| `phase` | string | See phase table below | Current lifecycle phase |
| `phase_progress` | float | 0.0-1.0 | How far through the current phase |
| `tick_count` | int | 0-40+ | Number of ticks elapsed |
| `p99_latency_ms` | float | 380-1780 | p99 latency in ms |
| `error_rate_pct` | float | 0.05-15.0 | Error rate in percent |
| `active_connections` | int | 118-200 | Active DB connections |
| `cache_hit_ratio` | float | 0.41-0.95 | Cache hit ratio |
| `auto_resolve` | bool | — | Whether this incident will auto-resolve |
| `request_id` | string | — | Request ID for this API call |

**Phase progression by kind:**

| Kind | Phase Sequence |
|---|---|
| `pool` | `climbing` → `plateau` → `recovering` → `resolved` |
| `cache` | `failover` → `warming` → `resolved` |
| `fraud` | `active` → `resolved` |

**Use this to:** Monitor the incident lifecycle in real time. Call every 2-3 seconds.

---

#### API #5: Resolve an Incident

**`POST /api/incidents/<kind>/resolve`** — Force-resolve an active incident.

**Parameters:**
- `<kind>` — One of `pool`, `cache`, `fraud`, or `current` (resolves whatever is running)

**Usage:**
```bash
# Resolve a specific kind
curl -s -X POST http://localhost:5001/api/incidents/pool/resolve | python3 -m json.tool

# Resolve whatever is running (don't need to know the kind)
curl -s -X POST http://localhost:5001/api/incidents/current/resolve | python3 -m json.tool
```

**Response format (success):**
```json
{
    "status": "resolved",
    "kind": "pool",
    "phase": "resolved",
    "request_id": "a1b2c3d4e5f6"
}
```

**Response format (no active incident):**
```json
{
    "status": "no_active_incident",
    "request_id": "a1b2c3d4e5f6"
}
```

**Response format (kind mismatch):**
```json
{
    "status": "no_active_incident",
    "expected": "pool",
    "active": "cache",
    "request_id": "a1b2c3d4e5f6"
}
```

**Step-by-step:**
1. If you know which kind is active: `curl -X POST http://localhost:5001/api/incidents/pool/resolve`
2. If you don't know: `curl -X POST http://localhost:5001/api/incidents/current/resolve`
3. Verify: `curl -s http://localhost:5001/api/incidents/state` → `"kind": "none"`

---

#### API #6: Random Incident

**`POST /api/incidents/trigger-random`** — Start a randomly selected scenario (pool/cache/fraud). Always auto-resolves.

**Usage:**
```bash
curl -s -X POST http://localhost:5001/api/incidents/trigger-random | python3 -m json.tool
```

**Response format:**
```json
{
    "status": "started",
    "kind": "pool",     # Random — could be pool, cache, or fraud
    "phase": "climbing",
    "tick_count": 0,
    "request_id": "a1b2c3d4e5f6"
}
```

**Use this to:** Test the AI agent's ability to identify an unknown incident.

---

### 2.2 Prometheus API (Port 9090)

---

#### API #7: Instant Query

**`GET /api/v1/query`** — Fetch the latest value for a metric.

**Usage:**
```bash
curl -s -G 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=checkout_p99_latency_ms{service="checkout-api"}' | python3 -m json.tool
```

**Response format:**
```json
{
    "status": "success",
    "data": {
        "resultType": "vector",
        "result": [
            {
                "metric": {
                    "__name__": "checkout_p99_latency_ms",
                    "service": "checkout-api"
                },
                "value": [
                    1710000000.0,
                    "380.0"
                ]
            }
        ]
    }
}
```

| Field | Meaning |
|---|---|
| `result[].metric.__name__` | Metric name |
| `result[].metric.service` | Service label (`checkout-api`) |
| `result[].value[0]` | Unix timestamp |
| `result[].value[1]` | Metric value (string) |

**Shortcut (get just the value):**
```bash
curl -s -G 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=checkout_p99_latency_ms{service="checkout-api"}' | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{float(d[\"data\"][\"result\"][0][\"value\"][1]):.1f}ms')"
```

---

#### API #8: Range Query

**`GET /api/v1/query_range`** — Fetch metric values over a time window.

**Usage:**
```bash
curl -s -G 'http://localhost:9090/api/v1/query_range' \
  --data-urlencode 'query=checkout_p99_latency_ms{service="checkout-api"}' \
  --data-urlencode 'start=1700000000' \
  --data-urlencode 'end=1700000900' \
  --data-urlencode 'step=60' | python3 -m json.tool
```

**Parameters:**
| Parameter | Format | Meaning |
|---|---|---|
| `query` | PromQL | Prometheus query expression |
| `start` | Unix timestamp | Start of time range |
| `end` | Unix timestamp | End of time range |
| `step` | seconds | Data point interval (default: Prometheus's step) |

**Convenience usage (all checkout metrics, last hour):**
```bash
curl -s -G 'http://localhost:9090/api/v1/query_range' \
  --data-urlencode 'query={__name__=~"checkout_.*",service="checkout-api"}' \
  --data-urlencode 'start=0' \
  --data-urlencode 'end=9999999999' | \
  python3 -c "
import sys,json
d=json.load(sys.stdin)
for r in d['data']['result']:
  name = r['metric']['__name__']
  vals = r['values']
  print(f'{name}: {len(vals)} points, latest={vals[-1][1]}')
"
```

---

#### API #9: Scrape Targets

**`GET /api/v1/targets`** — Check which targets Prometheus is scraping and their health.

**Usage:**
```bash
curl -s 'http://localhost:9090/api/v1/targets' | python3 -c "
import sys,json
d=json.load(sys.stdin)
for t in d['data']['activeTargets']:
  print(f'{t[\"health\"]:8s} {t[\"labels\"][\"job\"]} → {t[\"scrapeUrl\"]}')
"
```

**Expected output:**
```
up       flask-generator → http://flask-generator:5000/metrics
```

**Use this to:** Debug "no data in Prometheus" issues — if the target is `down`, check the FastAPI generator.

---

### 2.3 Loki API (Port 3100)

---

#### API #10: Query Logs

**`GET /loki/api/v1/query_range`** — Search logs by label filters.

**Usage:**
```bash
curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={service="checkout-api"}' \
  --data-urlencode 'limit=5' | python3 -m json.tool
```

**Parameters:**
| Parameter | Format | Default | Meaning |
|---|---|---|---|
| `query` | LogQL | (required) | Log query expression |
| `limit` | int | 100 | Max entries to return |
| `start` | Unix nanosecond | 1h ago | Start of time range |
| `end` | Unix nanosecond | now | End of time range |
| `direction` | `forward` / `backward` | `backward` | Sort order |

**Response format:**
```json
{
    "status": "success",
    "data": {
        "resultType": "streams",
        "result": [
            {
                "stream": {
                    "service": "checkout-api",
                    "source": "flask-generator"
                },
                "values": [
                    ["1710000000000000000", "{\"timestamp\":\"2026-07-16T14:30:00Z\",...}"],
                    ["1710000001000000000", "{\"timestamp\":\"2026-07-16T14:30:01Z\",...}"]
                ]
            }
        ]
    }
}
```

**Parsed output (more readable):**
```bash
curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={source="flask-generator"}' \
  --data-urlencode 'limit=10' | python3 -c "
import sys,json
d=json.load(sys.stdin)
for s in d['data']['result']:
  for ts,v in s['values']:
    entry = json.loads(v)
    print(f'{entry[\"level\"]:5s}  {entry[\"message\"][:55]}  | {entry.get(\"request_id\",\"?\")}')
"
```

**Available label filters:**
| Label | Value | Use |
|---|---|---|
| `service` | `checkout-api` | All logs from checkout service |
| `source` | `flask-generator` | Logs from the incident simulator |

**Common queries:**
```bash
# Trace by request ID
curl -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={source="flask-generator"} |= "a24bb972e4aa"'

# Filter by log level
curl -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={service="checkout-api"} |= "ERROR"'

# Filter by message content
curl -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={service="checkout-api"} |= "connection"'
```

---

#### API #11: List Labels

**`GET /loki/api/v1/labels`** — List all available log label names.

**Usage:**
```bash
curl -s http://localhost:3100/loki/api/v1/labels | python3 -m json.tool
```

**Expected response:**
```json
{
    "status": "success",
    "data": ["service", "source"]
}
```

**Use this to:** Verify Loki is running and has received data with the expected labels.

---

### 2.4 Grafana API (Port 3000)

Grafana API requires basic auth: `admin`/`admin`.

---

#### API #12: Health Check

**`GET /api/health`** — Verify Grafana is running.

**Usage:**
```bash
curl -s -u admin:admin 'http://localhost:3000/api/health' | python3 -m json.tool
```

**Expected response:**
```json
{
    "database": "ok"
}
```

---

#### API #13: Search Dashboards

**`GET /api/search`** — List all dashboards.

**Usage:**
```bash
curl -s -u admin:admin 'http://localhost:3000/api/search' | python3 -c "
import sys,json
for d in json.load(sys.stdin):
  print(f'  {d[\"title\"]} (uid={d[\"uid\"]}, folder={d.get(\"folderTitle\",\"General\")})')
"
```

**Expected output:**
```
  Incident Overview (uid=dfs7r8letxy4gb, folder=Incidents)
  Pool Exhaustion (uid=ffs7r8leyxqtca, folder=Incidents)
  Cache Failover (uid=dfs7r8lejycqoa, folder=Incidents)
  Fraud Outage (uid=afs7r8leoy5fkb, folder=Incidents)
```

---

#### API #14: Get Dashboard JSON

**`GET /api/dashboards/uid/<uid>`** — Fetch a dashboard's full JSON definition.

**Usage:**
```bash
curl -s -u admin:admin 'http://localhost:3000/api/dashboards/uid/dfs7r8letxy4gb' | \
  python3 -c "
import sys,json
d=json.load(sys.stdin)['dashboard']
print(f'Title: {d[\"title\"]}')
print(f'Panels: {len(d.get(\"panels\",[]))}')
for p in d.get('panels',[]):
  print(f'  - {p.get(\"title\",\"untitled\")} ({p.get(\"type\",\"?\")})')
"
```

**Expected output:**
```
Title: Incident Overview
Panels: 6
  - p99 Latency (timeseries)
  - Error Rate (timeseries)
  - Active Connections (timeseries)
  - Cache Hit Ratio (timeseries)
  - Errors by Type (bargauge)
  - Recent Error Logs (logs)
```

---

#### API #15: List Datasources

**`GET /api/datasources`** — List configured data sources.

**Usage:**
```bash
curl -s -u admin:admin 'http://localhost:3000/api/datasources' | python3 -c "
import sys,json
for d in json.load(sys.stdin):
  print(f'  {d[\"name\"]:12s} type={d[\"type\"]:12s} url={d[\"url\"]}')
"
```

**Expected output:**
```
  Prometheus    type=prometheus   url=http://prometheus:9090
  Loki          type=loki         url=http://loki:3100
```

---

## 3. Scenario A: Pool Exhaustion

**What it simulates:** A Postgres connection-pool leak. Connections climb from 118 toward 200 (the hard cap), causing latency and error rate to rise.

### 3.1 Trigger

```bash
curl -s -X POST http://localhost:5001/api/incidents/pool/trigger \
  -H 'Content-Type: application/json' \
  -d '{"auto_resolve":true}'
```

Save the request ID for tracing:

```bash
RID=$(curl -s -X POST http://localhost:5001/api/incidents/pool/trigger \
  -H 'Content-Type: application/json' \
  -d '{"auto_resolve":true}' | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['request_id'])")
echo "Request ID: $RID"
```

### 3.2 Lifecycle Timeline (~40 seconds)

| Time Elapsed | Tick | Phase | What's Happening |
|---|---|---|---|
| 0s | 0 | `climbing` | Just triggered — baseline metrics (118 conns, 380ms latency) |
| 5s | 5 | `climbing` | Connections rising: ~145, latency ~650ms, errors starting |
| 10s | 10 | `climbing` | Connections ~170, latency ~1.2s, errors ~3% |
| 15s | 15 | `plateau` | **Peak:** 200 connections, ~1.8s latency, ~6% errors |
| 22s | 22 | `plateau` | Still pinned at max — sustained degradation |
| 30s | 30 | `recovering` | Draining connections, metrics improving |
| 35s | 35 | `recovering` | Connections ~150, latency ~1s |
| 40s | 40 | `resolved` | Back to baseline |

### 3.3 Watch It Live

In a second terminal, poll the state every 3 seconds:

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

**Expected output:**
```
climbing     tick= 2  p99=  560.0ms  err= 1.02%  conns=129
climbing     tick= 5  p99=  810.0ms  err= 2.50%  conns=148
climbing     tick= 8  p99= 1080.0ms  err= 3.95%  conns=168
climbing     tick=11  p99= 1350.0ms  err= 5.10%  conns=184
climbing     tick=14  p99= 1600.0ms  err= 5.80%  conns=197
plateau      tick=17  p99= 1780.0ms  err= 6.10%  conns=200
plateau      tick=20  p99= 1750.0ms  err= 6.05%  conns=200
...
recovering   tick=32  p99= 1000.0ms  err= 3.10%  conns=160
resolved     tick=40  p99=  380.0ms  err= 0.05%  conns=118
```

### 3.4 Check Logs in Loki

#### Via HTTP API

```bash
# All pool logs for this request
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
print(f'=== Pool Exhaustion Logs (Request ID: {entries[0][\"request_id\"] if entries else \"?\"}) ===')
print(f'Total entries: {len(entries)}')
print()
for e in entries:
  print(f'  {e[\"level\"]:5s}  {e[\"message\"][:55]}  | rid={e.get(\"request_id\",\"?\")}')
"
```

**What you'll see:**
```
=== Pool Exhaustion Logs ===
Total entries: 18
  ERROR  could not obtain connection from pool within 5000ms  | rid=a24bb972e4aa
  ERROR  could not obtain connection from pool within 5000ms  | rid=a24bb972e4aa
  WARN   request exceeded p99 SLO threshold (1500ms)          | rid=a24bb972e4aa
  ERROR  could not obtain connection from pool within 5000ms  | rid=a24bb972e4aa
  ...
```

Key observations:
- **ERROR** level — "could not obtain connection from pool within 5000ms" (appears when connections >= 190)
- **WARN** level — "request exceeded p99 SLO threshold (1500ms)" (approximately 30% probability per tick — may not appear on every run)

#### Via Grafana Explore

1. Open http://localhost:3000 and log in with `admin`/`admin`
2. Click the **Explore** icon (compass, left sidebar)
3. Select **Loki** data source from the dropdown
4. Enter this LogQL query and click **Run query**:
   ```logql
   {source="flask-generator"} |= "a24bb972e4aa"
   ```
   (Replace `a24bb972e4aa` with your actual request ID)
5. Switch to **Logs** view — you'll see all log entries from that incident, color-coded by level

**To see ALL pool errors (not just one request):**
```logql
{service="checkout-api"} |= "could not obtain connection"
```

### 3.5 Check Grafana Dashboards

#### Grafana UI Quick Start (First Time)

If you've never opened Grafana before:

1. Open http://localhost:3000 in your browser
2. Log in with **Username:** `admin`, **Password:** `admin`
   - Grafana will prompt you to change the password on first login — you can skip this
3. You'll land on the **Home** dashboard. Click the **≡** (hamburger menu, top-left corner)
4. Go to **Dashboards** → you'll see the **Incidents** folder in the left panel
5. Click the **Incidents** folder to expand it and see all 4 dashboards

Once you've logged in once, you can go directly to any dashboard by its URL.

---

#### Step 1 — Open Incident Overview

Open http://localhost:3000/d/dfs7r8letxy4gb (or navigate: **≡ → Dashboards → Incidents → Incident Overview**)

This is your single pane of glass. All 6 panels refresh every 15 seconds. Set the time range to **Last 15 minutes** (dropdown in top-right corner).

**Trigger pool exhaustion** (if not already running):
```bash
curl -s -X POST http://localhost:5001/api/incidents/pool/trigger \
  -H 'Content-Type: application/json' -d '{"auto_resolve":true}'
```

Now watch the panels update over the next ~40 seconds:

| Panel | What the Line Graph Shows During Each Phase |
|---|---|
| **p99 Latency** (top-left) | **Climbing:** Green line rises from 380 → crosses orange threshold at 1000ms → crosses red threshold at 1500ms. **Plateau:** Flat at ~1780ms (red zone). **Recovering:** Line drops back through orange → green.
| **Error Rate** (top-right) | **Climbing:** Green line rises from 0.05% → crosses orange at 2% → crosses red at 5%. **Plateau:** Flat at ~6.1% (solid red). **Recovering:** Drops back.
| **Active Connections** (bottom-left) | **Climbing:** Single green line climbs from 118 → crosses orange at 150 → crosses red at 190. Hits 200 and goes flat. The dashed **max** line shows the ceiling. **Plateau:** Both lines flat at 200.
| **Cache Hit Ratio** (bottom-right) | **ALL phases:** Flat green line at ~0.95. **This staying flat** is your signal that cache is NOT involved.
| **Errors by Type** (bargauge) | During plateau, shows a horizontal bar labeled "pool_timeout" or "connection timeout" — this tells you what kind of errors are happening.
| **Recent Error Logs** (Loki panel) | Shows live log lines from Loki: `"could not obtain connection from pool within 5000ms"` — these update in near-real-time.

#### Step 2 — Drill into Pool Exhaustion Dashboard

Open http://localhost:3000/d/ffs7r8leyxqtca (or navigate via dashboard dropdown)

This dashboard has 4 focused panels:

| Panel | What to Look For |
|---|---|
| **Connections vs Max** (top-left) | The **active connections** line should approach and flatten at the **max connections** line (200). The green/orange/red threshold zones at 150 and 190 help you spot the severity at a glance. This is the SINGLE BEST PANEL for pool exhaustion.
| **p99 Latency & Pool Timeout Errors** (top-right) | Two series: latency (ms, left axis) and pool timeouts/sec (right axis). Watch both rise together during climbing phase. The latency thresholds (1000ms orange, 1500ms red) help you gauge severity.
| **Error Rate** (bottom-left) | Same as overview but in context. Should be climbing with connections.
| **Pool Error Logs** (bottom-right) | Loki log panel filtered to `"could not obtain connection"` — shows live error entries. If you see nothing here but the other panels show high values, Loki might be down.

#### Step 3 — Confirm with Grafana Explore (Ad-Hoc)

Grafana Explore lets you run raw PromQL/LogQL queries to confirm what you're seeing:

1. Click the **Explore** icon (compass, left sidebar)
2. Select **Prometheus** data source
3. Run this query to see connections approach the max:
   ```promql
   checkout_active_connections{service="checkout-api"}
   ```
   The line should climb from 118 to exactly 200 and flatten.

4. Switch the data source to **Loki** and run:
   ```logql
   {service="checkout-api"} |= "could not obtain connection"
   ```
   You should see 15-20 ERROR-level log entries with timestamps matching the climbing/plateau phases.

#### Step 4 — Verify Against Other Dashboards

To confirm this is pool exhaustion (not cache or fraud), quickly open the other dashboards:

| Dashboard | What You Should See | What Would Be Different If It Wasn't Pool |
|---|---|---|
| **Cache Failover** (UID: `dfs7r8lejycqoa`) | Cache Hit Ratio flat at ~0.95, Error Rate flat at ~0.05% | If it were cache, Cache Hit Ratio would show a step-change drop to 0.41 |
| **Fraud Outage** (UID: `afs7r8leoy5fkb`) | Error Rate would show ~6% (or climbing), not 10-15% | If it were fraud, Error Rate would spike to 10-15% |

The **Active Connections** panel on Incident Overview is the definitive signal: only pool exhaustion pushes connections near 200.

#### Grafana Dashboard URLs (Clickable)

| Dashboard | URL |
|---|---|
| Incident Overview | http://localhost:3000/d/dfs7r8letxy4gb |
| Pool Exhaustion | http://localhost:3000/d/ffs7r8leyxqtca |
| Cache Failover | http://localhost:3000/d/dfs7r8lejycqoa |
| Fraud Outage | http://localhost:3000/d/afs7r8leoy5fkb |

---

## 4. Scenario B: Cache Failover

**What it simulates:** A Redis cluster node fails. Cache hits drop from 95% to 41% in a step change. Error rates stay flat (cache misses don't cause errors, just slower responses). The cache gradually warms back up.

### 4.1 Trigger

```bash
RID=$(curl -s -X POST http://localhost:5001/api/incidents/cache/trigger \
  -H 'Content-Type: application/json' \
  -d '{"auto_resolve":true}' | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['request_id'])")
echo "Request ID: $RID"
```

### 4.2 Lifecycle Timeline (~18 seconds)

| Time Elapsed | Tick | Phase | What's Happening |
|---|---|---|---|
| 0s | 0 | `failover` | Cache hit drops instantly from 0.95 → 0.41 (step change) |
| 3s | 3 | `failover` | Cache hit flat at 0.41, latency ~930ms |
| 6s | 6 | `warming` | Cache starts warming: 0.41 → slowly rising |
| 10s | 10 | `warming` | Cache hit ~0.67, latency dropping |
| 14s | 14 | `warming` | Cache hit ~0.85, latency near baseline |
| 18s | 18 | `resolved` | Cache hit ~0.93, back to normal |

**Key difference from pool:** Error rate stays at ~0.05% the **entire time**. This is the distinguishing signal.

### 4.3 Watch It Live

```bash
while true; do
  curl -s http://localhost:5001/api/incidents/state | python3 -c \
    "import sys,json; d=json.load(sys.stdin); \
     print(f'{d[\"phase\"]:12s} tick={d[\"tick_count\"]:2d}  \
            cache={d[\"cache_hit_ratio\"]:>5.3f}  \
            p99={d[\"p99_latency_ms\"]:>6.1f}ms  \
            err={d[\"error_rate_pct\"]:>5.2f}%')"
  sleep 2
done
```

**Expected output:**
```
failover     tick= 1  cache=0.410  p99= 930.0ms  err= 0.05%
failover     tick= 3  cache=0.410  p99= 870.0ms  err= 0.04%
warming      tick= 7  cache=0.593  p99= 690.0ms  err= 0.07%
warming      tick=11  cache=0.772  p99= 500.0ms  err= 0.06%
warming      tick=15  cache=0.888  p99= 420.0ms  err= 0.05%
resolved     tick=18  cache=0.930  p99= 380.0ms  err= 0.05%
```

### 4.4 Check Logs in Loki

**Key difference from pool:** Cache failover produces **WARN** level logs, not ERROR. Error rate stays flat.

```bash
curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode "query={source=\"flask-generator\"} |= \"$RID\"" \
  --data-urlencode 'limit=30' | python3 -c "
import sys,json
d=json.load(sys.stdin)
entries=[]
for s in d['data']['result']:
  for ts,v in s['values']:
    entries.append(json.loads(v))
entries.sort(key=lambda x: x.get('timestamp',''))
print(f'=== Cache Failover Logs (Request ID: {entries[0][\"request_id\"] if entries else \"?\"}) ===')
print(f'Total entries: {len(entries)}')
print()
for e in entries:
  print(f'  {e[\"level\"]:5s}  {e[\"message\"][:55]}')
"
```

**What you'll see:**
```
=== Cache Failover Logs ===
Total entries: 3
  WARN   Redis cluster failover detected
  WARN   Redis cluster failover detected
  WARN   MOVED redirection error from cache node
```

Key observations:
- **Only WARN level** — no ERROR logs at all
- Messages are about Redis, not connections
- Fewer log entries than pool (~3 vs ~18) because cache warnings are probabilistic (4%/tick)

**In Grafana Explore:**
```logql
{service="checkout-api"} |= "Redis cluster failover"
```

### 4.5 Check Grafana Dashboards

#### Step 1 — Open Cache Failover Dashboard

Open http://localhost:3000/d/dfs7r8lejycqoa (or navigate: **≡ → Dashboards → Incidents → Cache Failover**)

Set the time range to **Last 15 minutes** (top-right dropdown). This dashboard has 4 focused panels specifically for cache incidents.

**Trigger cache failover** (if not already running):
```bash
curl -s -X POST http://localhost:5001/api/incidents/cache/trigger \
  -H 'Content-Type: application/json' -d '{"auto_resolve":true}'
```

Now watch the panels update over the next ~18 seconds:

| Panel | What to Look For |
|---|---|
| **Cache Hit Ratio** (top-left) | **This is THE definitive signal.** Watch for a **vertical step-change drop** from ~0.95 → 0.41 — it happens instantly at the start, not gradually. The line drops from the green zone (above 0.85) straight into the red zone (below 0.70) in a single data point. During **warming**, it climbs back from 0.41 → 0.93 over ~12 seconds. The thresholds (0.85 orange, 0.70 red) make the severity visible at a glance.
| **p99 Latency (Cache Impact)** (top-right) | Rises to ~930ms during failover (crossing the 600ms orange threshold, briefly touching 1000ms red threshold). Drops back to ~380ms during warming. Note: latency never reaches pool levels (~1780ms).
| **Error Rate (Should Stay Low)** (bottom-left) | **This is the key distinguishing signal.** Look for a flat line at ~0.05% — no spikes, no climbing. If you see errors here, it's NOT a pure cache failover. The Y-axis max is capped at 5% for emphasis.
| **Cache Warning Logs** (bottom-right) | Loki log panel filtered to logs containing "cache". You'll see 2-5 **WARN** level entries: `"Redis cluster failover detected"` and `"MOVED redirection error from cache node"`. Unlike pool where there are 15+ ERROR logs, cache produces only a handful of WARN logs.

#### Step 2 — Confirm on Incident Overview

Open http://localhost:3000/d/dfs7r8letxy4gb and look at these 3 panels side by side:

| Panel | What You See During Cache Failover | Why This Matters |
|---|---|---|
| **Cache Hit Ratio** | Sharp vertical drop, then gradual climb | The ONLY panel that shows a step change. No other scenario does this. |
| **Active Connections** | **Flat at 118** (normal) | If connections were rising, it would be pool exhaustion instead. |
| **Error Rate** | **Flat at ~0.05%** (normal) | If errors were spiking, it would be fraud or pool. Cache failover does NOT cause application errors. |

The combination of **(Cache Hit = step drop) + (Connections = flat) + (Error Rate = flat)** is unique to cache failover.

#### Step 3 — Verify with Grafana Explore

1. Click **Explore** (compass icon, left sidebar)
2. Select **Prometheus** data source
3. Run this query to see the cache hit step change:
   ```promql
   checkout_cache_hit_ratio{service="checkout-api"}
   ```
   The graph should show a sharp vertical drop (failover) followed by a gradual climb (warming).

4. Verify connections are normal:
   ```promql
   checkout_active_connections{service="checkout-api"}
   ```
   Flat line at 118 — no climbing.

5. Verify error rate is flat:
   ```promql
   checkout_error_rate_pct{service="checkout-api"}
   ```
   Flat line at 0.05% — no spikes.

6. Switch to **Loki** data source and check the logs:
   ```logql
   {service="checkout-api"} |= "Redis cluster failover"
   ```
   You'll see WARN-level entries, not ERROR.

#### Step 4 — Compare with Pool Dashboard

Switch to the **Pool Exhaustion** dashboard (http://localhost:3000/d/ffs7r8leyxqtca).

| Panel | Pool Exhaustion | Cache Failover (Current) |
|---|---|---|
| **Error Rate** | ~6% (high) | ~0.05% (flat) ✅ Confirms cache |
| **Connections** | 200 (maxed) | 118 (normal) ✅ Confirms cache |
| **Cache Hit** | 0.95 (normal) | 0.41 (step drop) ✅ Confirms cache |
| **Log Level** | ERROR | WARN ✅ Confirms cache |

---

## 5. Scenario C: Fraud Scoring Outage

**What it simulates:** An external fraud-scoring service goes down. Error rate spikes to 10-15%, but connections and cache hit remain normal. The distinguishing signal is extreme error rate with normal everything else.

### 5.1 Trigger

```bash
RID=$(curl -s -X POST http://localhost:5001/api/incidents/fraud/trigger \
  -H 'Content-Type: application/json' \
  -d '{"auto_resolve":true}' | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['request_id'])")
echo "Request ID: $RID"
```

### 5.2 Lifecycle Timeline (~20 seconds)

| Time Elapsed | Tick | Phase | What's Happening |
|---|---|---|---|
| 0s | 0 | `active` | Error rate jumps to ~12%, latency ~836ms |
| 5s | 5 | `active` | Error rate oscillates between 10-15% |
| 10s | 10 | `active` | Still active — sustained high error rate |
| 15s | 15 | `active` | Still active |
| 20s | 20 | `resolved` | Instant recovery — all metrics back to baseline |

**Key difference:** Connections stay at 118 and cache hit stays at 0.95 throughout. Only error rate and latency are affected.

### 5.3 Watch It Live

```bash
while true; do
  curl -s http://localhost:5001/api/incidents/state | python3 -c \
    "import sys,json; d=json.load(sys.stdin); \
     print(f'{d[\"phase\"]:12s} tick={d[\"tick_count\"]:2d}  \
            err={d[\"error_rate_pct\"]:>5.2f}%  \
            p99={d[\"p99_latency_ms\"]:>6.1f}ms  \
            conns={d[\"active_connections\"]:3d}  \
            cache={d[\"cache_hit_ratio\"]:>5.3f}')"
  sleep 2
done
```

**Expected output:**
```
active       tick= 2  err=12.80%  p99=836.0ms  conns=118  cache=0.950
active       tick= 5  err=14.10%  p99=830.0ms  conns=120  cache=0.948
active       tick= 8  err=11.30%  p99=840.0ms  conns=117  cache=0.951
active       tick=11  err=13.50%  p99=835.0ms  conns=119  cache=0.949
active       tick=14  err=10.80%  p99=838.0ms  conns=118  cache=0.952
active       tick=17  err=12.40%  p99=832.0ms  conns=117  cache=0.950
resolved     tick=20  err= 0.05%  p99=380.0ms  conns=118  cache=0.950
```

### 5.4 Check Logs in Loki

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
print(f'=== Fraud Outage Logs (Request ID: {entries[0][\"request_id\"] if entries else \"?\"}) ===')
print(f'Total entries: {len(entries)}')
print()
for e in entries:
  print(f'  {e[\"level\"]:5s}  {e[\"message\"][:55]}')
"
```

**What you'll see:**
```
=== Fraud Outage Logs ===
Total entries: 24
  ERROR  fraud-scoring-svc unavailable
  ERROR  fraud-scoring-svc unavailable
  ERROR  fraud-scoring-svc unavailable
  ERROR  fraud-scoring-svc unavailable
  ...
```

Key observations:
- **ERROR level** — "fraud-scoring-svc unavailable" (1-2 per tick, so ~20-40 entries total)
- Error message is specifically about the external fraud service, NOT about connections or Redis
- Many more error entries than pool (~24 vs ~18) because fraud fires every tick

**In Grafana Explore:**
```logql
{service="checkout-api"} |= "fraud-scoring-svc"
```

### 5.5 Check Grafana Dashboards

#### Step 1 — Open Fraud Outage Dashboard

Open http://localhost:3000/d/afs7r8leoy5fkb (or navigate: **≡ → Dashboards → Incidents → Fraud Outage**)

Set the time range to **Last 15 minutes** (top-right dropdown). This dashboard has 4 panels optimized for fraud detection.

**Trigger fraud outage** (if not already running):
```bash
curl -s -X POST http://localhost:5001/api/incidents/fraud/trigger \
  -H 'Content-Type: application/json' -d '{"auto_resolve":true}'
```

Now watch the panels update over the next ~20 seconds:

| Panel | What to Look For |
|---|---|
| **Error Rate (Fraud Spike)** (top-left) | **This is THE definitive signal.** Watch the line spike from 0.05% → **10-15%** instantly. Compare with pool (~6%) — fraud errors are ~2x higher. The Y-axis is capped at 20%, and the red threshold starts at 8%. The line will oscillate between 10-15% because fraud errors are generated with random noise (uniform 10-15%). When resolved, the line drops instantly back to 0.05%.
| **p99 Latency** (top-right) | Jumps to ~836ms (2.2× baseline). Note: this is LOWER than pool's ~1780ms because the fraud-scoring service is called in parallel — the main request still succeeds. No thresholds configured on this panel since latency impact is secondary.
| **Fraud Error Count** (bottom-left) | **Bargauge** panel showing the rate of `fraud_svc_unavailable` errors per second. During active phase, this shows a horizontal bar in the red zone. When resolved, it drops to zero. This panel tells you the error source is specifically the fraud-scoring service.
| **Fraud Error Logs** (bottom-right) | Loki log panel filtered to `"fraud-scoring-svc"`. Shows live log entries: `ERROR "fraud-scoring-svc unavailable"`. Expect 20-40 entries during a full lifecycle (1-2 per tick × 20 ticks).

#### Step 2 — Confirm on Incident Overview

Open http://localhost:3000/d/dfs7r8letxy4gb and look at these 3 panels:

| Panel | What You See During Fraud Outage | Why This Matters |
|---|---|---|
| **Error Rate** | **Spikes to 10-15%** — 2× higher than pool | Error rate >10% with normal everything else = fraud. This is the highest error rate of any scenario. |
| **Active Connections** | **Flat at 118** (normal) | If connections were rising, it would be pool exhaustion. Fraud doesn't affect connection pool. |
| **Cache Hit Ratio** | **Flat at ~0.95** (normal) | If cache hit dropped, it would be cache failover. Fraud doesn't affect Redis cache. |

The combination of **(Error Rate > 10%) + (Connections = flat at 118) + (Cache Hit = flat at 0.95)** is unique to fraud outage.

#### Step 3 — Verify with Grafana Explore

1. Click **Explore** (compass icon, left sidebar)
2. Select **Prometheus** data source
3. Run this query to see the extreme error rate:
   ```promql
   checkout_error_rate_pct{service="checkout-api"}
   ```
   You'll see values oscillating between 10 and 15 — much higher than pool's ~6%.

4. Verify connections are normal:
   ```promql
   checkout_active_connections{service="checkout-api"}
   ```
   Flat line at 118 — no climbing.

5. Switch to **Loki** data source and check the error logs:
   ```logql
   {service="checkout-api"} |= "fraud-scoring-svc"
   ```
   You should see 20-40 ERROR entries showing `"fraud-scoring-svc unavailable"`.

#### Step 4 — Compare All 3 Scenario Dashboards Side by Side

This is the fastest way to confirm fraud outage. Open each dashboard in a separate browser tab:

| Dashboard | Tab | What You'll See |
|---|---|---|
| **Pool Exhaustion** | Tab 1 | http://localhost:3000/d/ffs7r8leyxqtca → Connections near 200, errors at ~6% |
| **Cache Failover** | Tab 2 | http://localhost:3000/d/dfs7r8lejycqoa → Cache hit at 0.41, errors flat |
| **Fraud Outage** (current) | Tab 3 | http://localhost:3000/d/afs7r8leoy5fkb → **Error rate 10-15%, connections normal, cache normal** ✅ |

| Metric | Pool | Cache | Fraud (Current) |
|---|---|---|---|
| **Error Rate** | ~6% (moderate) | ~0.05% (flat) | **10-15%** (extreme) ✅ |
| **Active Connections** | **200** (maxed) | 118 (normal) | 118 (normal) ✅ |
| **Cache Hit Ratio** | 0.95 (normal) | **0.41** (step drop) | 0.95 (normal) ✅ |
| **Log Message** | "could not obtain connection" | "Redis cluster failover" | **"fraud-scoring-svc unavailable"** ✅ |
| **Log Level** | ERROR (many) | WARN (few) | **ERROR (many)** ✅ |

Only fraud shows all three: **extreme error rate + normal connections + normal cache**.

---

## 6. Quick Reference Tables

### 6.1 API Endpoints

| Action | Command | Port |
|---|---|---|
| Trigger pool | `curl -X POST http://localhost:5001/api/incidents/pool/trigger -H 'Content-Type: application/json' -d '{"auto_resolve":true}'` | 5001 |
| Trigger cache | `curl -X POST http://localhost:5001/api/incidents/cache/trigger -H 'Content-Type: application/json' -d '{"auto_resolve":true}'` | 5001 |
| Trigger fraud | `curl -X POST http://localhost:5001/api/incidents/fraud/trigger -H 'Content-Type: application/json' -d '{"auto_resolve":true}'` | 5001 |
| Random scenario | `curl -X POST http://localhost:5001/api/incidents/trigger-random` | 5001 |
| Get state | `curl http://localhost:5001/api/incidents/state` | 5001 |
| Resolve current | `curl -X POST http://localhost:5001/api/incidents/current/resolve` | 5001 |
| Prometheus query | `curl -G 'http://localhost:9090/api/v1/query_range' --data-urlencode 'query=checkout_p99_latency_ms{service="checkout-api"}'` | 9090 |
| Loki query | `curl -G 'http://localhost:3100/loki/api/v1/query_range' --data-urlencode 'query={service="checkout-api"}'` | 3100 |

### 6.2 Scenario Comparison Matrix

| Attribute | Pool Exhaustion | Cache Failover | Fraud Outage |
|---|---|---|---|
| **Duration** | ~40s | ~18s | ~20s |
| **Phases** | climbing → plateau → recovering | failover → warming | active |
| **Error rate** | Rises to ~6% | Flat at ~0.05% | Spikes to 10-15% |
| **Connections** | Climbs to 200 | Stays at 118 | Stays at 118 |
| **Cache hit** | Normal (~0.95) | Drops to 0.41 (step) | Normal (~0.95) |
| **Latency (peak)** | ~1780ms | ~930ms | ~836ms |
| **Log level** | ERROR | WARN | ERROR |
| **Log message** | "could not obtain connection" | "Redis cluster failover" | "fraud-scoring-svc unavailable" |
| **# of log entries** | ~15-20 | ~3-5 | ~20-40 |
| **Best dashboard** | Pool Exhaustion | Cache Failover | Fraud Outage |
| **Diagnostic PromQL** | `checkout_active_connections` | `checkout_cache_hit_ratio` | `checkout_error_rate_pct` |

### 6.3 LogQL Queries for Each Scenario

| What You Want | LogQL Query |
|---|---|
| All logs for a specific request | `{source="flask-generator"} \|= "a24bb972e4aa"` |
| Pool errors only | `{service="checkout-api"} \|= "could not obtain connection"` |
| Cache warnings only | `{service="checkout-api"} \|= "Redis cluster failover"` |
| Fraud errors only | `{service="checkout-api"} \|= "fraud-scoring-svc"` |
| All ERROR level logs | `{service="checkout-api"} \|= "ERROR"` |
| All logs from the last 5 minutes | `{service="checkout-api"}` (needs start/end timestamps in HTTP API — Grafana Explore handles this automatically) |
| Count errors per 5-minute bucket | `sum by (level) (count_over_time({service="checkout-api"}[5m]))` |

### 6.4 PromQL Queries for Each Scenario

| What You Want | PromQL Query |
|---|---|
| Current p99 latency | `checkout_p99_latency_ms{service="checkout-api"}` |
| Current error rate | `checkout_error_rate_pct{service="checkout-api"}` |
| Current connections | `checkout_active_connections{service="checkout-api"}` |
| Current cache hit ratio | `checkout_cache_hit_ratio{service="checkout-api"}` |
| All checkout metrics | `{__name__=~"checkout_.*",service="checkout-api"}` |

### 6.5 Grafana Dashboard UIDs

| Dashboard | UID | URL |
|---|---|---|
| Incident Overview | `dfs7r8letxy4gb` | http://localhost:3000/d/dfs7r8letxy4gb |
| Pool Exhaustion | `ffs7r8leyxqtca` | http://localhost:3000/d/ffs7r8leyxqtca |
| Cache Failover | `dfs7r8lejycqoa` | http://localhost:3000/d/dfs7r8lejycqoa |
| Fraud Outage | `afs7r8leoy5fkb` | http://localhost:3000/d/afs7r8leoy5fkb |

---

## 7. Troubleshooting

### 7.1 "No data" in Loki after triggering

**Check:** Did Loki receive the data?
```bash
curl -s -G 'http://localhost:3100/loki/api/v1/labels' | python3 -m json.tool
```

If the response shows `{"status":"success","data":["service","source"]}`, Loki is running but may not have ingested data yet. Wait a few seconds after triggering.

**Force clear:** If Loki has old data that's mixing with new:
```bash
docker compose stop loki
docker compose rm -f loki
docker volume rm incident-pilot_loki-data
docker compose up -d loki
sleep 8
```

### 7.2 "No data" in Grafana dashboards

**Check:** Are metrics reaching Prometheus?
```bash
curl -s -G 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=checkout_p99_latency_ms{service="checkout-api"}' | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Found {len(d[\"data\"][\"result\"])} series')"
```

**Check:** Is the FastAPI generator exposing metrics?
```bash
curl -s http://localhost:5001/metrics | grep checkout_p99
```

### 7.3 "I triggered but nothing happened"

```bash
# 1. Is an incident already running?
curl -s http://localhost:5001/api/incidents/state | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d['kind'])"

# 2. Resolve any existing incident first  
curl -s -X POST http://localhost:5001/api/incidents/current/resolve

# 3. Try again
curl -s -X POST http://localhost:5001/api/incidents/pool/trigger \
  -H 'Content-Type: application/json' -d '{"auto_resolve":true}'
```

### 7.4 "I see data for the wrong scenario"

Each trigger call returns a new request ID. Trace by request ID to isolate:
```bash
# Previous trigger still has entries in Loki. Filter by request ID:
curl -s -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={source="flask-generator"} |= "YOUR_RID"' \
  --data-urlencode 'limit=5'
```

### 7.5 Logs in Docker but not in Loki

```bash
# Check Loki log driver plugin
docker plugin ls | grep loki

# If missing, install:
docker plugin install grafana/loki-docker-driver:latest --alias loki --grant-all-permissions

# Check the flask-generator container log driver
docker inspect flask-generator --format '{{.HostConfig.LogConfig.Type}}'
# Expected: "loki"
```

---

*End of Incident Lifecycle Guide. Every command in this document is tested against the live stack.*
