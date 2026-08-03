"""
SQLite persistence for multi-turn chats.

A thin, dependency-free data layer behind the Gradio UI: it stores each chat
and its messages so a conversation survives a page refresh or a container
restart, and so the sidebar can list past chats to reopen.

Design notes:
- A fresh connection is opened per call (``check_same_thread=False``) because
  Gradio dispatches event handlers on worker threads -- there's no long-lived
  shared connection or lock to manage.
- The assistant message stores the per-turn trace dict as ``trace_json`` so
  the UI's trace panel can be reconstructed for any turn without a schema
  change later.
- ``recent_history`` returns raw ``(user, assistant)`` text pairs for the
  agent's sliding-window memory -- never the RAG-augmented form.

The DB path defaults to ``<repo>/chats/chats.db`` (a directory that can be
volume-mounted in Docker), overridable via the ``CHAT_DB_PATH`` env var.
"""

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent / "chats" / "chats.db"
DB_PATH = Path(os.environ.get("CHAT_DB_PATH", str(DEFAULT_DB_PATH)))

DEFAULT_TITLE = "New chat"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create the schema if it doesn't exist. Idempotent; called on import."""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    TEXT NOT NULL,
                role       TEXT NOT NULL,   -- 'user' | 'assistant'
                content    TEXT NOT NULL,
                trace_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_messages_chat
                ON messages(chat_id, id);
            """
        )


def create_chat(title: str = DEFAULT_TITLE) -> str:
    """Create a new chat and return its id."""
    chat_id = uuid.uuid4().hex
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO chats (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (chat_id, title, now, now),
        )
    logger.info("Created chat %s", chat_id)
    return chat_id


def list_chats() -> list[dict]:
    """All chats, most-recently-updated first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, updated_at FROM chats ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_messages(chat_id: str) -> list[dict]:
    """All messages for a chat, in order. Each dict has role/content/trace_json."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content, trace_json FROM messages "
            "WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def append_message(chat_id: str, role: str, content: str,
                   trace: dict | None = None) -> None:
    """Append a message and bump the chat's updated_at (so it floats to the
    top of the sidebar). ``trace`` is JSON-serialised onto the row."""
    trace_json = json.dumps(trace) if trace is not None else None
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (chat_id, role, content, trace_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, role, content, trace_json, now),
        )
        conn.execute(
            "UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id)
        )


def set_title(chat_id: str, title: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE chats SET title = ? WHERE id = ?", (title, chat_id))


def delete_chat(chat_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    logger.info("Deleted chat %s", chat_id)


def recent_history(chat_id: str, n: int) -> list[tuple[str, str]]:
    """The last ``n`` completed ``(user, assistant)`` turn pairs for a chat,
    oldest-first, as raw text -- for the agent's sliding-window memory.

    Messages are paired by walking the ordered log and matching each user
    message to the next assistant message. A trailing user message with no
    assistant reply yet (e.g. the turn currently being processed) is skipped.
    """
    if n <= 0:
        return []
    msgs = get_messages(chat_id)
    pairs: list[tuple[str, str]] = []
    pending_user: str | None = None
    for m in msgs:
        if m["role"] == "user":
            pending_user = m["content"]
        elif m["role"] == "assistant" and pending_user is not None:
            pairs.append((pending_user, m["content"]))
            pending_user = None
    return pairs[-n:]


# Ensure the schema exists as soon as the module is imported.
init_db()
