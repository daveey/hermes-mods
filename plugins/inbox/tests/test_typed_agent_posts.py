"""A resolve-typed card is no way around the ask contract for agent-owned posters (kanban/coach/cron).

kanban and cron may only use the decision types they actually post (registry allowed_types, no "*"), and an
agent-owned resolve-typed post still runs the status/defect wording backstop on its title. Tool and CLI doors.
"""
import argparse
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
COACH_TYPES = HERE.parents[1] / "skills" / "productivity" / "hermes-coach" / "inbox-types"
ASK = {"interaction": "approval", "why_human": "Only David can approve", "action": "Approve the thing"}
# Stand-ins for the private x-presence types: same names, resolve actions, minimal fields.
SOCIAL = {
    "x-post": {"name": "x-post", "fields": {"id": {"required": True}},
               "actions": [{"id": "approve", "label": "Approve", "kind": "resolve"}]},
    "x-reply-draft": {"name": "x-reply-draft", "fields": {"id": {"required": True}},
                      "actions": [{"id": "post", "label": "Post", "kind": "resolve"}]},
}


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for k in ("HERMES_KANBAN_TASK", "HERMES_DELEGATED_CHILD_CONTEXT"):
        monkeypatch.delenv(k, raising=False)
    types = tmp_path / "inbox" / "types"
    types.mkdir(parents=True)
    for f in COACH_TYPES.glob("*.yaml"):
        (types / f.name).write_text(f.read_text())
    for name, spec in SOCIAL.items():
        (types / f"{name}.yaml").write_text(json.dumps(spec))
    plugin = load("_tap_plugin", "__init__.py")
    st = plugin.store_module()
    cards = []
    monkeypatch.setattr(st, "kanban_task_creator", lambda **kw: cards.append(kw) or "t-x")
    return plugin, st, cards, tmp_path


def post(door, plugin, title, key, poster, type_name, fields, ask=None):
    """Returns True when posted."""
    if door == "tool":
        args = {"title": title, "body": "b", "accept_prompt": "go", "poster": poster, "key": key,
                "type": type_name, "fields": fields}
        if ask:
            args["ask"] = ask
        return bool(json.loads(plugin.handle_post(args)).get("posted"))
    parser = argparse.ArgumentParser()
    plugin._setup_cli(parser)
    argv = ["post", title, "--body", "b", "--prompt", "go", "--poster", poster, "--key", key, "--type", type_name]
    for k, v in fields.items():
        argv += ["--field", f"{k}={v}"]
    if ask:
        argv += ["--ask", json.dumps(ask)]
    return plugin._run_cli(parser.parse_args(argv)) == 0


BYPASS = [
    # reviewer probe cases (review round 3): no ask, resolve-typed, agent-owned
    ("kanban · fix things", "engine-budget", {"day": "2026-10-30"}, "Detector misses replies", "poster_type_forbidden"),
    ("kanban · x", "coach-improvement", {"id": "c1"}, "Detector misses replies", "poster_type_forbidden"),
    ("cron", "engine-budget", {"day": "2026-10-30"}, "Nightly job failed", "poster_type_forbidden"),
    ("coach", "engine-budget", {"day": "2026-10-30"}, "Coach detector misses replies", "content_defect"),
    # an allowed type still gets the wording backstop
    ("cron · x-daily-post", "x-post", {"id": "p1"}, "Nightly job failed", "content_status"),
    ("kanban · ship", "x-post", {"id": "p2"}, "Deploy is stuck on the hub", "content_defect"),
]


@pytest.mark.parametrize("door", ["tool", "cli"])
@pytest.mark.parametrize("poster,type_name,fields,title,why", BYPASS)
def test_agent_owned_resolve_typed_bypass_is_refused(env, door, poster, type_name, fields, title, why):
    plugin, st, cards, _ = env
    assert not post(door, plugin, title, "k:1", poster, type_name, fields)
    assert not st.Inbox().list()
    assert why in cards[0]["body"]


REAL = [
    ("cron · x-daily-post-and-engage", "x-post", {"id": "O-2"}, "X post pending: O-2, Wed Oct 21 3pm PT", None),
    ("cron", "x-reply-draft", {"id": "r1"},
     'X reply draft: @someone on "I can\'t see why trust fails here…"', None),
    ("kanban · highlight movies", "x-post", {"id": "CLIP-1"}, "Review demo video + caption (20 seconds)",
     None),
    ("coach · engine budget", "engine-budget", {"day": "2026-10-30"}, "Engine budget: total cap hit 2026-10-30", ASK),
    ("coach · improvement", "coach-improvement", {"id": "c9"}, "Rule: Never complete cards by punting live checks",
     None),
]


@pytest.mark.parametrize("door", ["tool", "cli"])
@pytest.mark.parametrize("poster,type_name,fields,title,ask", REAL)
def test_real_typed_agent_producers_are_still_accepted(env, door, poster, type_name, fields, title, ask):
    plugin, st, cards, _ = env
    assert post(door, plugin, title, "real:1", poster, type_name, fields, ask), cards
    assert len(st.Inbox().list()) == 1


@pytest.mark.parametrize("door", ["tool", "cli"])
def test_manual_mode_selfheal_proposal_is_still_accepted(env, door):
    """Self-heal titles describe a defect by design; selfheal_reason (auto_approve) is what gates them."""
    plugin, st, cards, home = env
    (home / "coach").mkdir()
    (home / "coach" / "selfheal-config.json").write_text(json.dumps({"auto_approve": False}))
    assert post(door, plugin, "Desktop backend logs a dropped connection as a crash", "selfheal:fp1",
                "self-heal", "selfheal", {"fp": "fp1"}), cards
    assert len(st.Inbox().list()) == 1


def test_kanban_and_cron_have_no_wildcard_type():
    reg = load("_tap_ac", "ask_contract.py").registry("/nonexistent")
    for fam in ("kanban", "cron"):
        assert "*" not in reg[fam]["allowed_types"], fam
