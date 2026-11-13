"""A card records the chat that posted it (``origin_session``), so a consumer can tell which chat owns it.

Incident: a chat made a Bluesky reply-draft card, then also asked "press Post" as a chat step. user-steps
could only link that step to the card by words in its title, because the card did not say which chat
made it. The posting chat's id comes from the terminal's per-command ``HERMES_SESSION_ID`` (the
``hermes inbox post`` CLI and scripts importing the store) or the tool call's session.
"""
import sqlite3

import pytest

pytestmark = pytest.mark.needs_core

from test_store import env, store  # noqa: F401  (fixture)


def origin(tmp_path, sid):
    with sqlite3.connect(tmp_path / "inbox" / "inbox.db") as c:
        return c.execute("SELECT origin_session FROM items WHERE session_id=?", (sid,)).fetchone()[0]


def test_post_records_posting_chat_from_env(env, tmp_path, monkeypatch):
    inbox, _, _ = env
    monkeypatch.setenv("HERMES_SESSION_ID", "chat-that-posted")
    r = inbox.post("Draft", "Body", "Review", key="draft:a")
    assert origin(tmp_path, r["session_id"]) == "chat-that-posted"


def test_conditional_post_records_posting_chat(env, tmp_path, monkeypatch):
    inbox, _, _ = env
    monkeypatch.setenv("HERMES_SESSION_ID", "chat-that-posted")
    r = inbox.post("Draft", "Body", "Review", key="draft:b", if_absent=True)
    assert origin(tmp_path, r["session_id"]) == "chat-that-posted"


def test_explicit_origin_wins_and_refresh_keeps_first(env, tmp_path, monkeypatch):
    inbox, _, _ = env
    monkeypatch.setenv("HERMES_SESSION_ID", "env-chat")
    r = inbox.post("Draft", "Body", "Review", key="draft:c", origin_session="tool-chat")
    assert origin(tmp_path, r["session_id"]) == "tool-chat"
    again = inbox.post("Draft 2", "Body 2", "Review", key="draft:c", origin_session="later-chat")
    assert again["session_id"] == r["session_id"]
    assert origin(tmp_path, r["session_id"]) == "tool-chat"


def test_no_chat_records_null(env, tmp_path, monkeypatch):
    inbox, _, _ = env
    monkeypatch.delenv("HERMES_SESSION_ID", raising=False)
    r = inbox.post("Draft", "Body", "Review", key="draft:d")
    assert origin(tmp_path, r["session_id"]) is None
