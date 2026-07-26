# IncidentPilot — Real-Time Incident Generator & Monitoring Stack

## Low-Level Design (LLD)

**Document Version:** 2.0  \
**Date:** 2026-07-16  \
**Author:** IncidentPilot Team  

---

## 1. Class Diagrams

### 1.1 FastAPI Generator — Core Classes

```
┌─────────────────────────────────────────────────────────────────────┐
│                        IncidentEngine                               │
├─────────────────────────────────────────────────────────────────────┤
│ - _active: Optional[ScenarioState]                                   │
│ - _lock: threading.RLock                                            │
├─────────────────────────────────────────────────────────────────────┤
│ + start_scenario(kind: str, auto_resolve: bool) -> ScenarioResult   │
│ + resolve(kind: Optional[str]) -> ScenarioResult                    │
│ + tick() -> None                                                     │
│ + get_state() -> Optional[ScenarioState]                             │
│ + get_state_dict() -> dict                                           │
│ + is_active() -> bool                                                │
│ - _advance_phase() -> None                                           │
│ - _compute_metric_values() -> None                                   │
│ - _compute_pool_metrics() -> None                                    │
│ - _compute_cache_metrics() -> None                                   │
│ - _compute_fraud_metrics() -> None                                   │
└──────────┬──────────────────────────────────────────────────────────┘
           │ 1
           │ has
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   ScenarioState (Pydantic BaseModel)                  │
├─────────────────────────────────────────────────────────────────────┤
│ + kind: str                    # "pool" | "cache" | "fraud"          │
│ + phase: str                   # "climbing" | "plateau" | ...       │
│ + phase_progress: float        # 0.0 → 1.0                          │
│ + tick_count: int              # Simulated minutes elapsed           │
│ + auto_resolve: bool           # True = auto-resolve after duration  │
│ + started_at: float            # time.time() of trigger              │
│                                                                      │
│ # Computed metric values (updated every tick)                        │
│ + p99_latency_ms: float                                              │
│ + error_rate_pct: float                                              │
│ + active_connections: int                                            │
│ + cache_hit_ratio: float                                             │
│                                                                      │
│ # Internal-only fields (excluded from serialization)                 │
│ + pool_error_pct: float     # (exclude=True)                         │
│ + fraud_error_pct: float    # (exclude=True)                         │
│ + cache_warn_pct: float     # (exclude=True)                         │
│                                                                      │
│ # Duration budgets are module-level constants in config.py,          │
│ # not stored on the model:                                           │
│ # POOL_CLIMBING_MINUTES = 15                                        │
│ # POOL_PLATEAU_MINUTES = 15                                         │
│ # POOL_RECOVERY_MINUTES = 10                                        │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                       MetricsExporter (module)                       │
├─────────────────────────────────────────────────────────────────────┤
│ + REGISTRY: CollectorRegistry                                        │
│ + p99_latency: Gauge           (checkout_p99_latency_ms)             │
│ + error_rate: Gauge            (checkout_error_rate_pct)             │
│ + active_connections: Gauge    (checkout_active_connections)         │
│ + max_connections: Gauge       (checkout_max_connections)            │
│ + cache_hit_ratio: Gauge       (checkout_cache_hit_ratio)            │
│ + errors_total: Counter        (checkout_errors_total)               │
│ + request_duration_ms: Histo.  (checkout_request_duration_ms)        │
├─────────────────────────────────────────────────────────────────────┤
│ + update_all(state: ScenarioState) -> None                           │
│ + reset_all() -> None                                                │
│ + get_metrics_output() -> tuple                                      │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                       LogGenerator (class)                           │
├─────────────────────────────────────────────────────────────────────┤
│ - _pool_error_msg: str = "could not obtain connection..."            │
│ - _cache_warn_msgs: list[str]                                        │
├─────────────────────────────────────────────────────────────────────┤
│ + emit_logs(state: ScenarioState) -> None                            │
│ │   (prints to stdout + pushes to Loki via HTTP)                     │
│ + _push_to_loki(lines: list[dict]) -> None                           │
│ │   (direct HTTP push to Loki API — more reliable than Docker       │
│ │    log driver plugin; uses incrementing nanosecond timestamps      │
│ │    to prevent Loki deduplication within the same stream)           │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 Data Flow Diagram (per tick)

```
┌─────────────────────────────────────────────────────────────────────┐
│                        TICK CYCLE (~1s or 60s)                      │
│                                                                      │
│  1. engine.tick()                                                    │
│     │                                                                │
│     ├─ If no active scenario → _update_baseline()                    │
│     │     Sets gauges to baseline values (latency ~380ms, etc.)      │
│     │                                                                │
│     ├─ If scenario active → advance phase if needed                  │
│     │     ├─ Check tick_count vs duration budgets                    │
│     │     ├─ Update phase_progress                                   │
│     │     └─ Transition: climbing → plateau → recovering → resolved  │
│     │                                                                │
│     └─ _compute_metric_values()                                      │
│         ├─ _compute_pool_metrics() — or                             │
│         ├─ _compute_cache_metrics() — or                            │
│         ├─ _compute_fraud_metrics()                                 │
│         └─ Updates state.p99_latency_ms, state.active_connections,  │
│            state.error_rate_pct, state.cache_hit_ratio              │
│                                                                      │
│  2. metrics_exporter.update_all(state)                               │
│     ├─ p99_latency.labels(...).set(state.p99_latency_ms)            │
│     ├─ error_rate.labels(...).set(state.error_rate_pct)             │
│     ├─ active_conns.labels(...).set(state.active_connections)       │
│     ├─ cache_hit.labels(...).set(state.cache_hit_ratio)             │
│     └─ max_conns.labels(...).set(MAX_CONNECTIONS)                   │
│                                                                      │
│  3. log_generator.emit_logs(state)                                   │
│     ├─ Derive ERROR logs if active_connections >= 190               │
│     ├─ Derive WARN logs if p99_latency_ms > 1500                    │
│     ├─ Print JSONL to stdout                                         │
│     └─ Push JSONL to Loki via _push_to_loki() (HTTP POST)           │
│                                                                      │
│  4. (External, async) Prometheus scrapes /metrics                   │
│  5. (External, async) Loki ingests via Docker log driver + HTTP push │
│  6. (External, async) Grafana refreshes dashboard panels            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Detailed Metric Computation Formulas

All formulas below reproduce the math from `synthetic-data/script/generate_synthetic_data.py` exactly.

### 2.1 Baseline (No Active Incident)

```
p99_latency_ms   = 380 + N(0, 15)
error_rate_pct   = max(0, 0.05 + N(0, 0.02))
active_connections = 118 + randint(-5, 5)
cache_hit_ratio  = round(0.95 + N(0, 0.01), 3)
```

### 2.2 Pool Exhaustion Scenario

```
# Phase boundaries (simulated minutes)
CLIMBING   = 0 → 15    (active_connections: 118 → 200)
PLATEAU    = 15 → 30   (pinned at max)
RECOVERING = 30 → 40   (connections drain)
RESOLVED   = 40+

# Climbing phase (progress = tick_count / climbing_minutes)
active_connections = min(200, int(118 + progress * (200 - 118)))
p99_latency_ms     = round(max(0, 380 + progress * 1450 + N(0, 40)), 1)
error_rate_pct     = round(max(0, 0.05 + progress * 5.8 + N(0, 0.15)), 3)

# Plateau phase (pinned)
active_connections = 200
p99_latency_ms     = round(max(0, 1780 + N(0, 60)), 1)
error_rate_pct     = round(max(0, 6.1 + N(0, 0.3)), 3)

# Recovering phase (progress = (tick_count - 30) / 10)
active_connections = int(200 - progress * (200 - 118))
p99_latency_ms     = round(max(0, 1780 - progress * 1400 + N(0, 30)), 1)
error_rate_pct     = round(max(0, 6.1 - progress * 6.0 + N(0, 0.1)), 3)

# Pool errors (connection acquisition timeouts) only appear when conns >= 190
pool_error_pct = uniform(4.0, 6.0) * (conns - 190) / (200 - 190)  if conns >= 190
pool_error_pct = 0                                                   if conns < 190
```

### 2.3 Cache Failover Scenario

```
# Phase boundaries
FAILOVER = 0 → 6   (cache_hit_ratio drops to floor)
WARMING  = 6 → 18  (ratio warms back up)
RESOLVED = 18+

# Failover phase
cache_hit_ratio = 0.41   (floor)

# Warming phase (progress = (tick_count - 6) / 12)
cache_hit_ratio = round(0.41 + progress * (0.93 - 0.41), 3)

# Latency impact (for both phases)
severity = (0.95 - cache_hit_ratio) / (0.95 - 0.41)
# baseline success_median_ms = ~240ms
success_median_ms = 240 * (1 + severity * 2.9)

# Error rate stays at baseline level — no error spike during cache incidents
# Cache WARN logs appear when cache_hit_ratio < 0.90
cache_warn_pct = 4.0  if cache_hit_ratio < 0.90
```

### 2.4 Fraud Scoring Outage Scenario

```
# Phase boundaries
ACTIVE   = 0 → 20  (fraud errors firing)
RESOLVED = 20+

# Active phase
error_rate_pct     = uniform(10.0, 15.0)       # all 503s from fraud-svc
p99_latency_ms     = baseline * 2.2             # ~836ms
active_connections = baseline (~118)            # not affected
cache_hit_ratio    = baseline (~0.95)           # not affected

# Resolved: back to baseline immediately
```

---

## 3. Log Derivation Logic

Every tick, the `LogGenerator` derives structured JSON log lines from the current `ScenarioState`:

### 3.1 Pool Error Logs

```python
# Emitted when: active_connections >= 190 AND phase is climbing or plateau
{
    "timestamp":   UTC_ISO8601,
    "service":     "checkout-api",
    "level":       "ERROR",
    "message":     "could not obtain connection from pool within 5000ms",
    "active_connections": state.active_connections,   # from state, not re-rolled
    "max_connections":    200,
}
```

### 3.2 Latency SLO Warning Logs

```python
# Emitted when: p99_latency_ms > 1500
# Probability: 30% per tick during plateau phase
{
    "timestamp":      UTC_ISO8601,
    "service":        "checkout-api",
    "level":          "WARN",
    "message":        "request exceeded p99 SLO threshold (1500ms)",
    "p99_latency_ms": state.p99_latency_ms,  # from state, not re-rolled
}
```

### 3.3 Cache Failover Warning Logs

```python
# Emitted when: cache_hit_ratio < 0.90
# Probability: 4% per tick
{
    "timestamp": UTC_ISO8601,
    "service":   "checkout-api",
    "level":     "WARN",
    "message":   "Redis cluster failover detected",  # or "MOVED redirection error from cache node"
}
```

### 3.4 Fraud Error Logs

```python
# Emitted when: fraud scenario active AND fraud_error_pct > 0
# 1-2 errors per tick
{
    "timestamp": UTC_ISO8601,
    "service":   "checkout-api",
    "level":     "ERROR",
    "message":   "fraud-scoring-svc unavailable",
}
```

### 3.5 Log Delivery: Dual Push

Logs are delivered to Loki via **two independent mechanisms** for reliability:

```python
def emit_logs(self, state: ScenarioState) -> None:
    lines = []  # derive from state (see 3.1-3.4)

    # 1. Print to stdout (for Docker logs visibility)
    for line in lines:
        print(json.dumps(line), flush=True)

    # 2. Push directly to Loki via HTTP (more reliable than Docker log driver)
    self._push_to_loki(lines)

def _push_to_loki(self, lines: list[dict]) -> None:
    """Push a batch of log lines to Loki via the HTTP push API.

    Each line gets a unique nanosecond timestamp (base_ns + i)
    so Loki does NOT deduplicate them as identical entries.
    """
    if not lines:
        return
    base_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    stream = {
        "streams": [{
            "stream": {"service": "checkout-api", "source": "flask-generator"},
            "values": [
                [str(base_ns + i), json.dumps(line)]
                for i, line in enumerate(lines)
            ],
        }]
    }
    try:
        resp = requests.post(LOKI_PUSH_URL, json=stream, timeout=2)
    except requests.RequestException:
        pass  # non-blocking; logs are best-effort
```

---

## 4. Prometheus Metric Definitions

### 4.1 Metric Registry

```python
from prometheus_client import Gauge, Counter, Histogram, CollectorRegistry

REGISTRY = CollectorRegistry()

checkout_p99_latency_ms = Gauge(
    "checkout_p99_latency_ms", "p99 request latency in milliseconds",
    ["service"], registry=REGISTRY
)

checkout_error_rate_pct = Gauge(
    "checkout_error_rate_pct", "Request error rate in percent",
    ["service"], registry=REGISTRY
)

checkout_active_connections = Gauge(
    "checkout_active_connections", "Current active database connections",
    ["service"], registry=REGISTRY
)

checkout_max_connections = Gauge(
    "checkout_max_connections", "Maximum database connections",
    ["service"], registry=REGISTRY
)

checkout_cache_hit_ratio = Gauge(
    "checkout_cache_hit_ratio", "Redis cache hit ratio",
    ["service"], registry=REGISTRY
)

checkout_errors_total = Counter(
    "checkout_errors_total", "Total errors by type",
    ["service", "error_type"], registry=REGISTRY
)

checkout_request_duration_ms = Histogram(
    "checkout_request_duration_ms", "Request duration in milliseconds",
    ["service", "endpoint"],
    buckets=(100, 250, 500, 1000, 1500, 2000, 3000, 5000),
    registry=REGISTRY
)
```

### 4.2 Metric Version (Prevents Phase Leakage)

The `phase` field is explicitly **NOT** exposed as a Prometheus metric label.
This ensures the IncidentPilot agent cannot cheat by reading `phase` — it
must infer the incident stage from the metric shapes alone.

---

## 5. FastAPI Application Structure

### 5.1 `app.py` — Application Entry Point

```python
# Pseudocode structure
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

app = FastAPI(title="Incident Generator", version="3.0.0")

# Global instances
engine = IncidentEngine()
log_generator = LogGenerator()

# Request-ID middleware
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    rid = _generate_rid()
    _request_id_ctx.set(rid)
    response = await call_next(request)
    return response

# Background tick thread: advances scenario state and emits metrics/logs
def _tick_loop():
    while True:
        engine.tick()
        state = engine.get_state()
        if state:
            update_all(state)           # Prometheus metrics
            log_generator.emit_logs(state)  # JSONL logs to stdout + Loki push
        time.sleep(TICK_INTERVAL_SECONDS)

threading.Thread(target=_tick_loop, daemon=True, name="tick-loop").start()

# Routes (see API spec in HLD)
@app.get("/metrics")
def metrics_endpoint():
    return PlainTextResponse(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST
    )

@app.get("/health")
def health():
    return _json_resp(HealthResponse(service="flask-generator", active_incident=active))

@app.post("/api/incidents/{kind}/trigger")
def trigger_incident(kind: str, body: TriggerRequest = TriggerRequest()):
    ...  # FastAPI validates the Pydantic body automatically

@app.post("/api/incidents/{kind}/resolve")
def resolve(kind: str):
    ...

@app.post("/api/incidents/trigger-random")
def trigger_random():
    ...

@app.get("/api/incidents/state")
def state():
    ...

# Pydantic models (config.py) stay the same:
# TriggerRequest, TriggerResponse, ResolveResponse,
# HealthResponse, StateResponse, ErrorResponse
```

**Key changes from Flask:**
- `@app.get(...)` / `@app.post(...)` instead of `@app.route(...)`
- `PlainTextResponse` for Prometheus metrics instead of Flask `Response(mimetype=...)`
- `JSONResponse` instead of `jsonify(...)`
- Pydantic models as FastAPI dependencies for automatic validation + OpenAPI docs
- ASGI middleware instead of Flask `before_request`
- `uvicorn` server instead of Flask's built-in WSGI server
- Auto-generated OpenAPI docs at `/docs` and `/redoc`

### 5.2 Thread Safety

The `IncidentEngine` is accessed from both:
- The background tick thread (reads + writes `active` state)
- FastAPI route handlers (reads `state`; writes `start_scenario` / `resolve` via POST)

**Synchronization**: A `threading.RLock` protects the `active` field. All
public methods (`tick()`, `start_scenario()`, `resolve()`, `get_state()`)
acquire this lock.

### 5.3 OpenAPI Documentation

FastAPI automatically generates OpenAPI documentation:
- **Swagger UI**: http://localhost:5001/docs
- **ReDoc**: http://localhost:5001/redoc

All request/response schemas are derived from the Pydantic models in `config.py`.

---

## 6. Docker Configuration Details

### 6.1 `docker-compose.yml` Network Topology

All services share a single `monitoring` bridge network. Service discovery
uses container names as DNS:

```
flask-generator:5000  ← Prometheus scrape target
prometheus:9090       ← Grafana datasource
loki:3100             ← Grafana datasource + Docker log driver + HTTP push target
```

### 6.2 Prometheus Scrape Config

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'flask-generator'
    static_configs:
      - targets: ['flask-generator:5000']
        labels:
          service: 'checkout-api'
```

### 6.3 Loki Configuration

```yaml
auth_enabled: false
server:
  http_listen_port: 3100
  grpc_listen_port: 9095
  log_level: info
ingester:
  wal:
    dir: /loki/wal
  lifecycler:
    address: 127.0.0.1
    ring:
      kvstore:
        store: inmemory
      replication_factor: 1
  chunk_idle_period: 30m
  chunk_retain_period: 1m
schema_config:
  configs:
    - from: 2024-01-01
      store: boltdb-shipper
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
    directory: /loki/chunks
compactor:
  working_directory: /loki/compactor
limits_config:
  reject_old_samples: true
  reject_old_samples_max_age: 168h
  ingestion_rate_mb: 10
  ingestion_burst_size_mb: 20
  allow_structured_metadata: false
```

### 6.4 Log Ingestion: Dual Delivery

Logs reach Loki via **two independent paths** for reliability:

**Path 1 — Docker log driver (primary):**
The FastAPI generator's stdout is forwarded to Loki via the Docker `loki` log
driver plugin:

```yaml
services:
  flask-generator:
    logging:
      driver: loki
      options:
        loki-url: "http://loki:3100/loki/api/v1/push"
        loki-retries: "3"
        loki-max-backoff: "1s"
```

The `loki` Docker log driver plugin must be installed before starting the
stack:
```bash
docker plugin install grafana/loki-docker-driver:latest --alias loki --grant-all-permissions
```

**Path 2 — Direct HTTP push (fallback):**
The `LogGenerator._push_to_loki()` method POSTs log lines directly to
`http://loki:3100/loki/api/v1/push` via the `requests` library. This is
a code-level push that works even when the Docker plugin is not installed
or misconfigured. The push URL is configurable via `LOKI_PUSH_URL` env var.

**Why two paths?** The Docker log driver plugin is reliable in production
but requires `docker plugin install` which can fail silently. The code-level
HTTP push is guaranteed to work as long as the container can reach Loki's
push endpoint over the Docker network.

**Timestamp deduplication:** Loki deduplicates entries with identical
timestamps within the same stream. The `_push_to_loki()` method gives each
line in a batch an incrementing nanosecond timestamp (`base_ns + i`) to
prevent silent data loss.

---

## 7. Grafana Provisioning

### 7.1 Datasource Provisioning

**`grafana/provisioning/datasources/prometheus.yml`**:
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

**`grafana/provisioning/datasources/loki.yml`**:
```yaml
apiVersion: 1
datasources:
  - name: Loki
    type: loki
    access: proxy
    url: http://loki:3100
    editable: false
```

### 7.2 Dashboard Provisioning

**`grafana/provisioning/dashboards/dashboard.yml`** (dashboard provider):
```yaml
apiVersion: 1
providers:
  - name: 'Incident Dashboards'
    orgId: 1
    folder: 'Incidents'
    type: file
    disableDeletion: true
    editable: true
    options:
      path: /etc/grafana/provisioning/dashboards
```

Four dashboard JSON files are provisioned:

| File | Panels | Key Queries |
|---|---|---|
| `incident-overview.json` | p99 latency, error rate, active connections, cache hit ratio | All `checkout_*` metrics, overlaid |
| `pool-exhaustion.json` | Connections vs max (area), pool timeout errors, latency distribution | `checkout_active_connections`, `checkout_p99_latency_ms` |
| `cache-failover.json` | Cache hit ratio step-change, latency overlay, cache warning count | `checkout_cache_hit_ratio`, `checkout_p99_latency_ms` |
| `fraud-outage.json` | 503 error rate spike, fraud-svc log count (Loki), latency SLO | `checkout_error_rate_pct`, Loki `{service="checkout-api"} \|~ "fraud-scoring"` |

---

## 8. IncidentPilot Agent Integration

### 8.1 Updated `query_logs()` Tool

```python
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100")

def query_logs(service: str, timeframe: str) -> dict:
    """
    Query live metrics from Prometheus and logs from Loki.
    Falls back to static files if either is unavailable.

    Source label logic:
      - "live":             both Prometheus and Loki responded
      - "static_fallback":  at least one data source fell back to files
      - "unavailable":      no data source responded at all
    """
    result = {"metrics": None, "logs": None, "source": "live"}

    # 1. Query Prometheus for metrics
    prom_data = query_prometheus(service, timeframe)
    if prom_data is not None:
        result["metrics"] = prom_data
    else:
        fallback = _load_metrics_fallback(service)
        if fallback is not None:
            result["metrics"] = fallback
            result["source"] = "static_fallback"
        else:
            result["source"] = "unavailable"

    # 2. Query Loki for logs
    loki_data = query_loki(service, timeframe)
    if loki_data is not None:
        result["logs"] = loki_data
    else:
        fallback = _load_logs_fallback(service)
        if fallback is not None:
            result["logs"] = fallback
            if result["source"] != "unavailable":
                result["source"] = "static_fallback"

    return result

def query_prometheus(service, timeframe):
    """
    Query Prometheus via a label matcher regex.

    NOTE: was previously an ``or``-chained PromQL query, but ``or`` only
    returns the leftmost metric that has data. The ``__name__=~`` regex
    matches ALL ``checkout_*`` metrics in a single request.
    """
    promql = f'{{__name__=~"checkout_.*",service="{service}"}}'
    params = {"query": promql, "start": ..., "end": ..., "step": "60"}
    try:
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query_range",
                          params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()["data"]["result"]
        # Strip any 'phase' labels that might leak
        for series in data:
            series["metric"].pop("phase", None)
        return data
    except (requests.ConnectionError, requests.Timeout, KeyError, ValueError):
        return None  # triggers fallback
```

### 8.2 Log Analysis Integration

Instead of dumping raw log lines into the LLM prompt, `IncidentPilot` uses
`analyze_logs()` from `query_logs.py` to produce a structured summary:

```python
def _format_live_data(self, logs_result: dict) -> str:
    source_label = logs_result.get("source", "unavailable")
    lines = []

    # Format metrics (latest values)
    metrics = logs_result.get("metrics")
    if metrics:
        for series in metrics[:8]:  # up to 8 metric series
            name = series["metric"]["__name__"]
            values = series.get("values", [])
            if values:
                lines.append(f"  {name}: {values[-1][1]} (latest)")

    # Analyze and format logs
    logs = logs_result.get("logs")
    if logs:
        analysis = analyze_logs(logs)  # structured analysis
        # Prints: Log level breakdown, error rate, top patterns, clusters

    return "[Data source: " + source_label + "]\n" + "\n".join(lines)
```### 8.3 Gradio UI Data Source Badge

The Gradio UI (app.py) prepends a source badge to every response:

| Badge | Meaning | Detected When |
|---|---|---|
| 🟢 **Data source: Live (Prometheus + Loki)** | Both Prometheus and Loki are reachable | `query_logs()` returns `source="live"` |
| 🟡 **Data source: Static files (fallback)** | At least one data source fell back to static files | `source="static_fallback"` |
| 🔴 **Data source: Unavailable** | No data responded at all | `source="unavailable"` |

The badge is determined by calling `query_logs()` exactly once before the
LLM invocation. The same result object is then passed to `pilot.query()`
as `logs_result` to avoid querying Prometheus/Loki twice per request.

### 8.4 Trace Panel

The Gradio UI includes an expandable **Agent Trace** accordion (closed by default)
that shows exactly what the agent saw:

- **Request ID** — copy this to trace logs end-to-end via grep or Loki
- **Data source** — 🟢/🟡/🔴 badge
- **RAG chunks** — source file, section header, content snippet (150 chars)
- **Live metrics** — name/value snapshot of all returned metrics
- **Log analysis** — total entries, level breakdown, error rate, top patterns, error clusters
- **Full LLM prompt** — the exact `HumanMessage` sent to Groq (truncated at 2000 chars)

```python
def _format_trace(trace: dict) -> str:
    # Builds Markdown with sections for:
    # - 🚨 Contradiction Detected (if any)
    # - Request ID
    # - Data source badge
    # - Retrieved RAG chunks
    # - Live metrics snapshot
    # - Log analysis
    # - Full LLM prompt
```

### 8.5 Request-ID Tracking

Every Gradio query and every FastAPI API call gets a unique request ID (12 hex chars)
for cross-service log correlation.

**Gradio side (`src/request_context.py`, `src/app.py`):**

```python
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="")

def set_request_id(rid: str | None = None) -> str:
    """Generate UUID hex[:12] and store in thread-local context."""
    ...

class RequestIdFilter(logging.Filter):
    """Inject request_id into every log record."""
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True
```

The filter is added to the root logger in `logging_config.py` so every
`logger.info()` call across all modules automatically includes `[req=...]`.

**FastAPI side (`flask-generator/app.py`):**

Uses `setLogRecordFactory()` to inject the request ID into every log record:

```python
_old_factory = logging.getLogRecordFactory()
def _record_factory(*args, **kwargs) -> logging.LogRecord:
    record = _old_factory(*args, **kwargs)
    record.request_id = _get_rid()
    return record
logging.setLogRecordFactory(_record_factory)
```

A FastAPI middleware handler generates a UUID for every API call (except `/metrics`).
All responses include `request_id` via the `_json_resp()` helper which injects
it into the Pydantic model's serialized output.
The trigger's `request_id` is stored in `ScenarioState` and flows into every
log entry emitted by `log_generator.emit_logs()` — enabling end-to-end tracing
from API trigger → Docker logs → Loki entries.

### 8.4 Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `PROMETHEUS_URL` | `http://localhost:9090` | Prometheus server URL |
| `LOKI_URL` | `http://localhost:3100` | Loki server URL |

---

## 9. Configuration Constants

All configurable constants live in `flask-generator/config.py`:

```python
# Metric baselines
MAX_CONNECTIONS = 200
BASELINE_LATENCY_MS = 380
BASELINE_ERROR_PCT = 0.05
BASELINE_CONNECTIONS = 118
SERVICE = "checkout-api"

# Timing (environment-configurable)
TICK_MODE = os.getenv("TICK_MODE", "accelerated")  # "realtime" | "accelerated"
_interval = 1 if TICK_MODE == "accelerated" else 60
TICK_INTERVAL_SECONDS = int(os.getenv("TICK_INTERVAL", str(_interval)))
SIM_MINUTES_PER_TICK = 1

# Phase duration defaults (in simulated minutes)
POOL_CLIMBING_MINUTES = 15
POOL_PLATEAU_MINUTES = 15
POOL_RECOVERY_MINUTES = 10
CACHE_FAILOVER_MINUTES = 6
CACHE_WARMING_MINUTES = 12
FRAUD_ACTIVE_MINUTES = 20

# Log message templates
POOL_ERROR_MSG = "could not obtain connection from pool within 5000ms"
FRAUD_ERROR_MSG = "fraud-scoring-svc unavailable"
LATENCY_SLO_WARN_MSG = "request exceeded p99 SLO threshold (1500ms)"
CACHE_WARN_MESSAGES = [
    "Redis cluster failover detected",
    "MOVED redirection error from cache node",
]

# Random number generators (deterministic seed for reproducibility)
RNG = random.Random(42)                   # main stream
WEEK_RNG = random.Random(1337)            # reserved for week dataset

# Loki push endpoint (inside Docker network)
LOKI_PUSH_URL = os.getenv("LOKI_PUSH_URL", "http://loki:3100/loki/api/v1/push")

# Phase stripping is done in query_logs.py's query_prometheus() function
# which pops any "phase" label from Prometheus response data. The FastAPI
# generator never exposes the phase as a Prometheus label — the agent
# must infer the incident stage from metric shapes.
```

---

## 10. Testing Strategy

### 10.1 Existing Unit Tests

The project has **two test suites** (67 tests total):

| Test File | Tests | What It Validates |
|---|---|---|
| `tests/test_query_logs.py` | 43 | Prometheus/Loki queries, static fallback, `parse_timeframe`, `analyze_logs` (mock all network calls) |
| `tests/test_incident_pilot.py` | 24 | Guardrail behaviour (2 real Groq API calls), system prompt structure (5), contradiction detection (17 — pure unit tests, no LLM) |

All 67 tests pass. Run:
```bash
.venv/bin/python -m pytest tests/ -v
```

**Contradiction detection tests** (17 tests) cover:
- `_parse_live_metrics` — extraction, empty data, malformed values
- `_classify_data` — pool, cache, fraud, normal classifications with metric thresholds
- `_classify_user_query` — keyword matching for all 3 incident types + no match
- `_build_contradiction_text` — matching, mismatching, and null cases
- `_detect_contradictions` — end-to-end integration with realistic data

### 10.2 FastAPI Generator Tests (Planned, Not Yet Built)

The following tests for the FastAPI generator are **planned but not yet implemented**:

| Test | What It Validates |
|---|---|
| `test_pool_scenario_lifecycle` | Pool scenario progresses through all 4 phases correctly |
| `test_cache_scenario_lifecycle` | Cache scenario failover → warming → resolved |
| `test_fraud_scenario_lifecycle` | Fraud scenario active → resolved |
| `test_log_derivation_pool` | ERROR logs emitted when conns >= 190 |
| `test_log_derivation_latency` | WARN logs emitted when latency > 1500 |
| `test_loki_push` | `_push_to_loki()` HTTP POST with correct payload format |
| `test_loki_timestamp_dedup` | Incrementing timestamps per batch entry |
| `test_no_phase_in_metrics` | Phase label not present in Prometheus output |
| `test_resolve_no_incident` | Resolve with no active incident returns graceful message |

These would be added as a `tests/test_flask_generator.py` file in a future
iteration.

---

## 11. Deployment & Operations

### 11.1 Startup Sequence

1. `docker compose up` starts all 4 containers
2. Flask generator is the first dependency; Prometheus, Loki, and Grafana wait for `depends_on`
3. Flask starts in baseline mode (no active incident)
4. Prometheus begins scraping `/metrics` every 15s
5. Loki begins accepting log pushes (both Docker log driver + HTTP push)
6. Grafana starts with pre-provisioned datasources and dashboards

### 11.2 Health Check

```bash
# Check FastAPI (port 5000 inside Docker, mapped to 5001 on host)
curl http://localhost:5001/health

# Check Prometheus targets
curl http://localhost:9090/api/v1/targets | jq '.data.activeTargets[].health'

# Check Loki readiness
curl http://localhost:3100/ready

# Check Grafana
curl http://admin:admin@localhost:3000/api/health
```

### 11.3 Logs

```bash
# View Flask generator logs
docker compose logs -f flask-generator

# View all services
docker compose logs -f

# Check Loki has ingested logs
curl -s "http://localhost:3100/loki/api/v1/query_range?query={service=%22checkout-api%22}&limit=5"

# Verify Loki labels from HTTP push
curl -s "http://localhost:3100/loki/api/v1/labels"
```
