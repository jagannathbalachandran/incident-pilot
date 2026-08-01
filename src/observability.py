"""
observability.py

Arize Phoenix tracing setup for IncidentPilot.

Tracing is **opt-in and off by default** -- it turns on only when
``PHOENIX_TRACING`` is truthy in the environment (loaded from ``.env`` at repo
root, same as ``GROQ_API_KEY``). When it's off, the ``@track``-decorated
methods in ``incident_pilot.py`` are zero-overhead passthroughs -- ``phoenix``
and ``openinference`` are never even imported -- so the app behaves exactly
as it would without this module.

Unlike some other tracers, Phoenix needs **no Docker and no API key** for
local use: ``phoenix.launch_app()`` starts a lightweight server on a
background thread inside this same process (default UI at
http://localhost:6006), and ``LangChainInstrumentor`` patches LangChain
globally, so every ``ChatGroq.invoke()`` is auto-traced with no per-model
callback wiring required. If you'd rather point at an already-running Phoenix
instance (local or Phoenix Cloud), set ``PHOENIX_COLLECTOR_ENDPOINT``
yourself and the in-process server is skipped.

Enabling tracing sends prompts (which include retrieved runbook/postmortem
text and live telemetry) and responses to wherever Phoenix is configured to
export spans -- by default that's the local in-process server only, but set
``PHOENIX_COLLECTOR_ENDPOINT`` / ``PHOENIX_API_KEY`` to point at Phoenix Cloud
instead.

Relevant env vars:
    PHOENIX_TRACING=true          # our master switch (off by default)
    PHOENIX_COLLECTOR_ENDPOINT=... # optional; defaults to the local in-process server
    PHOENIX_PROJECT_NAME=...       # optional; defaulted below
    PHOENIX_API_KEY=...            # only needed for Phoenix Cloud
    PHOENIX_PORT=...               # local server port (default 6006)
"""

import functools
import logging
import os
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_PROJECT = "incident-pilot"
LOCAL_PORT = 6006

# Values treated as "tracing on" for PHOENIX_TRACING.
_TRUTHY = {"1", "true", "yes", "on"}

# Built lazily, once, the first time tracing is confirmed enabled.
_tracer = None


def tracing_enabled() -> bool:
    """True if Phoenix tracing is switched on via ``PHOENIX_TRACING``.

    Checked at call time (not import time) so it's robust to ``.env`` being
    loaded after this module is imported.
    """
    return os.environ.get("PHOENIX_TRACING", "").strip().lower() in _TRUTHY


def _set_default_env(name: str, value: str) -> None:
    """Like ``os.environ.setdefault``, but also fires when the var is present
    but empty -- docker-compose's ``${VAR:-}`` pattern always sets the key,
    just to an empty string, so a plain ``setdefault`` would never apply our
    defaults inside a container."""
    if not os.environ.get(name):
        os.environ[name] = value


def _get_tracer():
    """Build (once) the OpenInference tracer used by ``track()``.

    Launches a local, in-process Phoenix server (no Docker) unless an
    external endpoint is already configured via ``PHOENIX_COLLECTOR_ENDPOINT``.
    Also globally instruments LangChain so every ``ChatGroq.invoke()`` is
    captured with token usage, nested under whatever ``@track`` span is
    active.
    """
    global _tracer
    if _tracer is not None:
        return _tracer

    endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
    if not endpoint:
        # docker-compose's ${VAR:-} pattern leaves this set to an empty
        # string rather than unset -- Phoenix's own launch_app() treats a
        # present-but-empty value as "configured" and warns that traces will
        # go elsewhere, so clear it before calling in.
        os.environ.pop("PHOENIX_COLLECTOR_ENDPOINT", None)
        port = int(os.environ.get("PHOENIX_PORT") or LOCAL_PORT)
        _set_default_env("PHOENIX_PORT", str(port))
        import phoenix as px
        session = px.launch_app()  # reads port from PHOENIX_PORT (launch_app's port= kwarg is deprecated)
        base_url = (session.url if session else f"http://localhost:{port}/").rstrip("/")
        # Must be the full OTLP traces path with an explicit protocol below --
        # letting register() infer both from a bare host:port URL picked the
        # wrong wire protocol in testing (spans silently failed to export).
        endpoint = f"{base_url}/v1/traces"
        _set_default_env("PHOENIX_COLLECTOR_ENDPOINT", endpoint)
        logger.info("Launched local Phoenix server -- view traces at %s", base_url)

    project_name = os.environ.get("PHOENIX_PROJECT_NAME") or DEFAULT_PROJECT
    _set_default_env("PHOENIX_PROJECT_NAME", project_name)

    from phoenix.otel import register
    tracer_provider = register(
        endpoint=endpoint,
        protocol="http/protobuf",
        project_name=project_name,
        batch=False,
        verbose=False,
    )

    from openinference.instrumentation.langchain import LangChainInstrumentor
    LangChainInstrumentor().instrument(tracer_provider=tracer_provider)

    _tracer = tracer_provider.get_tracer(__name__)
    return _tracer


def track(type: str = "chain", name: str | None = None) -> Callable[[Callable], Callable]:
    """Opt-in tracing decorator.

    When ``PHOENIX_TRACING`` is off, the wrapped function runs untouched and
    ``phoenix``/``openinference`` are never imported. When on, wraps the
    function in an OpenInference span of the given ``type`` -- one of
    ``"chain"``, ``"tool"``, ``"retriever"``, ``"llm"`` (matching
    ``OITracer``'s span-kind decorators) -- built once on first call, then
    reused.

    Usage: ``@track(type="tool", name="mcp_tool")``.
    """

    def decorator(func: Callable) -> Callable:
        wrapped: Callable | None = None  # real tracer.<type>(...)(func), built once

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            nonlocal wrapped
            if not tracing_enabled():
                return func(*args, **kwargs)
            if wrapped is None:
                tracer = _get_tracer()
                span_decorator = getattr(tracer, type)
                wrapped = span_decorator(name=name or func.__name__)(func)
            return wrapped(*args, **kwargs)

        return wrapper

    return decorator


def update_trace_metadata(metadata: dict | None = None) -> None:
    """Attach metadata (e.g. request_id, model) to the current span, so
    traces line up with the app's ``[req=<id>]`` logs. No-op when tracing is
    disabled or there's no active span."""
    if not tracing_enabled() or not metadata:
        return
    try:
        from opentelemetry import trace as otel_trace
        span = otel_trace.get_current_span()
        for key, value in metadata.items():
            if value is not None:
                span.set_attribute(key, value)
    except Exception:
        logger.debug("Phoenix span.set_attribute failed", exc_info=True)


def configure_observability() -> None:
    """Set up Phoenix tracing and log its status.

    Call once at startup, right after ``setup_logging()``. Safe to call when
    tracing is disabled (it just logs that it's off). When enabled, this
    eagerly launches the local Phoenix server (if needed) and instruments
    LangChain, rather than waiting for the first ``@track``-decorated call, so
    the logged status reflects what's actually wired up.
    """
    if not tracing_enabled():
        logger.info("Phoenix tracing disabled (set PHOENIX_TRACING=true to enable)")
        return

    _get_tracer()
    logger.info(
        "Phoenix tracing enabled (project=%s, endpoint=%s)",
        os.environ.get("PHOENIX_PROJECT_NAME", DEFAULT_PROJECT),
        os.environ.get("PHOENIX_COLLECTOR_ENDPOINT"),
    )
