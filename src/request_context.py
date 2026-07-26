"""
request_context.py

Thread-safe request-ID tracking for the entire triage pipeline.

Uses ``contextvars`` (Python 3.7+) so each Gradio request thread gets its own
request ID without any global mutable state. A ``logging.Filter`` subclass
reads the active ID and injects it into every log record — no need to pass
the ID explicitly to any function.

Usage:
    from request_context import RequestIdFilter, set_request_id

    # At request entry point:
    rid = set_request_id()

    # In logging_config.py:
    handler.addFilter(RequestIdFilter())
    formatter = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  [req=%(request_id)s]  %(name)s  %(message)s",
        ...
    )

Every logger.info() / .debug() / .warning() call in any module will now
automatically include the active request ID tag.
"""

import logging
import uuid
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Return the request ID for the current thread (or ``""`` if none set)."""
    return _request_id.get()


def set_request_id(rid: str | None = None) -> str:
    """Set a request ID for the current thread.

    Args:
        rid: Optional explicit ID (e.g. from an API caller).  If None, a
             short UUID (12 hex chars) is generated.

    Returns:
        The request ID that was set.
    """
    if rid is None:
        rid = uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


class RequestIdFilter(logging.Filter):
    """Logging filter that injects ``request_id`` into every log record.

    The ``request_id`` attribute is read from the thread-local context via
    ``get_request_id()``.  It shows up in the log format via ``%(request_id)s``.

    When no request is active (e.g. during startup or background tasks),
    the tag renders as an empty string ``[]``.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True
