"""Briefs must not order a fresh review for every new head of a state PR.

ship-only-guard carries an exact-head approval across update-branch merges of
`state` when the PR's verbatim patch-id is unchanged and the rollup is green. A
card brief or steer that says "any new head needs a fresh exact-head verdict"
overrides that carry in the worker's eyes and buys one review round per base
refresh (each round gives the base time to drift again).
"""
import importlib.util
from pathlib import Path

import pytest

_HERE = Path(__file__).parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, _HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


brief = _load("ship_only_brief", "brief.py")
plugin = _load("ship_only_plugin", "__init__.py")

# Shapes of the real texts that caused six extra review rounds.
CARD_BODY = (
    "Build, review and land the updater switch.\n"
    "2. Open a PR to `state`; PR body enumerates every call site.\n"
    "3. Create an independent exact-head review card pinned model X.\n"
    "4. On APPROVE and green state CI at the exact head: land per state-pr-landing.md. "
    "Any new head needs a fresh exact-head verdict.\n")
STEER = (
    "Review close-out: reviewer approved #42 exact 60e50cf0, merge refused base drift. "
    "Resume existing owner scope: read exact guard refusal, ordinary branch refresh preserving "
    "the patch, required fresh exact-head review/CI then guarded landing.")
WORKER_ECHO = (
    "Ran the prescribed update-branch and read back new OPEN head 56c71efa. Fresh review card "
    "created. Any further head requires fresh exact-head Opus verdict.")


@pytest.mark.parametrize("text", [CARD_BODY, STEER, WORKER_ECHO])
def test_unconditional_per_head_review_mandate_is_refused(text):
    why = brief.check("kanban_create", {"title": "t", "body": text})
    assert why and "patch-id" in why and "no-carry:" in why
    assert brief.check("kanban_comment", {"task_id": "t_1", "body": text})


def test_plugin_hook_blocks_the_brief():
    out = plugin._pre_tool_call("kanban_create", {"title": "t", "body": CARD_BODY})
    assert out and out["action"] == "block" and "carr" in out["message"]


@pytest.mark.parametrize("text", [
    # The carry rule itself, as the owner rewrote it.
    "If the merge guard forces another update-branch, prove the PR's own patch-id is unchanged; "
    "a fresh exact-head review is needed only if the patch-id differs.",
    # A conflict-resolved refresh really does need a new review.
    "PR to `state`: any new head needs a fresh exact-head verdict when the refresh resolved a conflict.",
    # Deliberate exception with its reason.
    "PR to `state`. no-carry: David asked for a review of every head. Any new head needs a fresh review.",
    # Status report, no mandate.
    "Fresh mandatory exact-head review card t_1 created for #42 after update-branch.",
    # Not about a state PR or a base refresh at all.
    "Any new head needs a fresh exact-head verdict.",
])
def test_conditional_or_unrelated_text_passes(text):
    assert brief.check("kanban_create", {"body": text}) is None
    assert brief.check("kanban_comment", {"body": text}) is None


def test_other_tools_and_bad_args_pass():
    assert brief.check("terminal", {"command": CARD_BODY}) is None
    assert brief.check("kanban_create", None) is None
    assert brief.check("kanban_create", {"body": 3}) is None
