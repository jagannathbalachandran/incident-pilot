"""
logging_config.py

Shared logging configuration for all IncidentPilot modules.

Injects a ``RequestIdFilter`` from ``request_context`` so every log line
carries the active request ID — enabling cross-module traceability for
debugging triage queries end to end.

Usage:
    from logging_config import setup_logging

    setup_logging()
    # Now logging.getLogger(__name__) works with consistent formatting across
    # all modules, and every log line includes [req=<id>] when a request is
    # active, or [req=-] during startup / background tasks.
"""

import logging
import sys

from request_context import RequestIdFilter


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger with a standardised format.

    Args:
        level: Logging level (default: logging.INFO).
               Set to logging.DEBUG for verbose tracing.
    """
    # Avoid adding duplicate handlers if called multiple times
    root = logging.getLogger()
    if root.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  [req=%(request_id)s]  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())

    root.setLevel(level)
    root.addHandler(handler)
