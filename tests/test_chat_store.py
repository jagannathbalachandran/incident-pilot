"""
Tests for the SQLite chat store (src/chat_store.py).

Each test runs against a throwaway on-disk SQLite file in a temp dir -- no
network, no shared state. `chat_store.DB_PATH` is a module global read by
`_connect()`, so we point it at the temp file and re-init the schema per test.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import chat_store


class TestChatStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db_path = chat_store.DB_PATH
        chat_store.DB_PATH = Path(self._tmp.name) / "chats.db"
        chat_store.init_db()

    def tearDown(self):
        chat_store.DB_PATH = self._orig_db_path
        self._tmp.cleanup()

    def test_create_and_list(self):
        cid = chat_store.create_chat("pool exhaustion")
        chats = chat_store.list_chats()
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0]["id"], cid)
        self.assertEqual(chats[0]["title"], "pool exhaustion")

    def test_append_and_get_messages(self):
        cid = chat_store.create_chat()
        chat_store.append_message(cid, "user", "checkout p99 high")
        chat_store.append_message(cid, "assistant", "pool exhaustion", trace={"source": "live"})
        msgs = chat_store.get_messages(cid)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[0]["content"], "checkout p99 high")
        self.assertEqual(msgs[1]["role"], "assistant")
        # Trace is JSON-serialised onto the assistant row.
        self.assertIn("live", msgs[1]["trace_json"])
        self.assertIsNone(msgs[0]["trace_json"])

    def test_list_orders_by_recent_activity(self):
        a = chat_store.create_chat("first")
        b = chat_store.create_chat("second")
        # Touch `a` after `b` -> `a` should float to the top.
        chat_store.append_message(a, "user", "later message")
        titles = [c["title"] for c in chat_store.list_chats()]
        self.assertEqual(titles[0], "first")
        self.assertEqual(titles[1], "second")

    def test_recent_history_pairs_and_windows(self):
        cid = chat_store.create_chat()
        chat_store.append_message(cid, "user", "u1")
        chat_store.append_message(cid, "assistant", "a1")
        chat_store.append_message(cid, "user", "u2")
        chat_store.append_message(cid, "assistant", "a2")
        chat_store.append_message(cid, "user", "u3")
        chat_store.append_message(cid, "assistant", "a3")

        self.assertEqual(
            chat_store.recent_history(cid, 3),
            [("u1", "a1"), ("u2", "a2"), ("u3", "a3")],
        )
        # Windowing keeps only the last n pairs.
        self.assertEqual(chat_store.recent_history(cid, 2), [("u2", "a2"), ("u3", "a3")])
        self.assertEqual(chat_store.recent_history(cid, 0), [])

    def test_recent_history_skips_trailing_unanswered_user(self):
        """A user message with no assistant reply yet (the turn being
        processed) must not appear as a (user, assistant) pair."""
        cid = chat_store.create_chat()
        chat_store.append_message(cid, "user", "u1")
        chat_store.append_message(cid, "assistant", "a1")
        chat_store.append_message(cid, "user", "u2-pending")  # no reply yet
        self.assertEqual(chat_store.recent_history(cid, 5), [("u1", "a1")])

    def test_set_title(self):
        cid = chat_store.create_chat()
        chat_store.set_title(cid, "renamed")
        self.assertEqual(chat_store.list_chats()[0]["title"], "renamed")

    def test_delete_chat_cascades_messages(self):
        cid = chat_store.create_chat()
        chat_store.append_message(cid, "user", "hello")
        chat_store.delete_chat(cid)
        self.assertEqual(chat_store.list_chats(), [])
        # Messages are gone too (FK ON DELETE CASCADE).
        self.assertEqual(chat_store.get_messages(cid), [])

    def test_empty_history_for_new_chat(self):
        cid = chat_store.create_chat()
        self.assertEqual(chat_store.recent_history(cid, 3), [])


if __name__ == "__main__":
    unittest.main()
