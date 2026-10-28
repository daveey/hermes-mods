"""A relayed standing instruction ("dont block, dont wait") travels as explicit `standing:` lines.

Self-heal agent-aa8d66631c: the receiving session's compaction paraphrased a relayed "dont block, dont
wait" into its opposite. Core keeps `standing:` lines word for word, so the relay must emit them.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from test_cross_session_args import relay, sm  # noqa: F401  (fixture + loaded plugin)

WORDS = "get this session work done. dont block, dont wait. dont accept tasks that are waiting for something"


def test_quoted_user_directive_gets_a_standing_line(relay):
    msg = f"David DIRECT NOW: '{WORDS}'. I inspected hub: B t_ae11c0bc todo gated PR44."
    sm.handle({"action": "send", "target": "@session:default/20261023_054025_d312e6", "message": msg})
    sent = relay[-1]["args"]["message"]
    assert sent.startswith(msg)
    assert f"\nstanding: {WORDS}" in sent


def test_agent_prose_without_user_attribution_is_not_tagged(relay):
    msg = "Status: CI is green, don't wait on me for the merge."
    sm.handle({"action": "send", "target": "@session:default/20261023_054025_d312e6", "message": msg})
    assert relay[-1]["args"]["message"] == msg


def test_existing_standing_line_is_not_duplicated(relay):
    msg = f"David said '{WORDS}'.\nstanding: {WORDS}"
    sm.handle({"action": "send", "target": "@session:default/20261023_054025_d312e6", "message": msg})
    assert relay[-1]["args"]["message"].count("standing:") == 1
