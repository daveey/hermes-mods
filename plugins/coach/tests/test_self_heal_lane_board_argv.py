"""Lane writes go through lane_kanban_argv: a digest board '<gateway>:<board>' is never passed to `--board` raw.

Digest-sourced lanes hold '<gateway>:<board>' (live 11-13: 53 hub lanes on 'hermes-hub:default', 11 on
'macbook:default'). `hermes kanban --board hermes-hub:default ...` exits 2 (invalid slug), so the occurrence comment,
the SQLite incident comment and the inbox-step note on such an owner failed. This host's own gateway now uses the
board part; another gateway's card is skipped with a logged reason instead of being written on the local board.
"""
import json
from types import SimpleNamespace

import pytest

from test_self_heal_lane import HUB, NOW, OPEN, OLD_STEP, core, defect, ev, sh, store  # noqa: F401 (fixture)


class SlugBoard:
    """Fake `hermes kanban` that enforces the real CLI's slug check: a ':' in `--board` exits 2. `show --json`
    returns an open card whose body still carries the retired inbox step; comment succeeds."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        if "--board" in argv and ":" in argv[argv.index("--board") + 1]:
            return SimpleNamespace(returncode=2, stdout="", stderr="kanban: error: invalid board slug")
        if "show" in argv:
            tid = argv[argv.index("show") + 1]
            return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {
                "id": tid, "status": "running", "body": "Self-heal FIX\n" + OLD_STEP}}), stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def comments(self):
        return [a for a in self.calls if "comment" in a]


@pytest.fixture
def here(monkeypatch):
    """This host is the hub: digest host 'hermes-hub', Board gateway 'hub'."""
    monkeypatch.setattr(sh, "host_name", lambda: "hermes-hub")
    monkeypatch.setattr(sh._placement_evidence, "home_machine", lambda: "hub")


def test_lane_kanban_argv(here):
    base = ["hermes", "kanban"]
    assert sh.lane_kanban_argv(None) == (base, None)
    assert sh.lane_kanban_argv("bb") == (base + ["--board", "bb"], None)
    assert sh.lane_kanban_argv("hermes-hub:default") == (base + ["--board", "default"], None)
    assert sh.lane_kanban_argv("Hermes-Hub:bb") == (base + ["--board", "bb"], None)
    assert sh.lane_kanban_argv("hub:default") == (base + ["--board", "default"], None)  # the Board gateway name
    assert sh.lane_kanban_argv("hermes-hub:") == (base, None)
    argv, why = sh.lane_kanban_argv("macbook:default")
    assert argv is None and "macbook" in why


def test_unreadable_board_identity_still_knows_the_host(monkeypatch):
    monkeypatch.setattr(sh, "host_name", lambda: "hermes-hub")

    def boom():
        raise sh._placement_evidence.HomePinUnresolved("no identity")
    monkeypatch.setattr(sh._placement_evidence, "home_machine", boom)
    assert sh.lane_kanban_argv("hermes-hub:default")[0] == ["hermes", "kanban", "--board", "default"]
    assert sh.lane_kanban_argv("hub:default")[0] is None


def _occurrence():
    d = defect("gateway", "gateway restarted into a live gateway", source="signal", quote="", session_id=None,
               comment_open_card=True)
    d["fingerprint"] = "gateway-cc28b19bca"
    return d


def _lane(store, board):
    sh.LaneState(store.root).update("gateway-cc28b19bca", status="approved", card="t_abcdef10", fix_card="t_abcdef11",
                                  component="gateway", symptom="restart storm", board=board)


def _file(d, store, runner):
    return sh.file_defect(d, store=store, m="live", runner=runner, now=NOW, cfg=HUB, budget_fn=OPEN,
                          owner_fn=lambda *a: [], sig_owner_fn=lambda *a: [])


def test_occurrence_comment_on_a_local_digest_board_lands(store, here):
    _lane(store, "hermes-hub:default")
    b = SlugBoard()
    out = _file(_occurrence(), store, b)
    assert out["action"] == "skip" and out["card"] == "t_abcdef11"
    (c,) = b.comments()
    assert c[:4] == ["hermes", "kanban", "--board", "default"] and c[c.index("comment") + 1] == "t_abcdef11"
    assert ev(store, "selfheal_occurrence_comment")[-1]["ok"] is True


def test_occurrence_comment_on_a_remote_gateway_is_skipped_and_logged(store, here):
    _lane(store, "macbook:default")
    b = SlugBoard()
    out = _file(_occurrence(), store, b)
    assert out["action"] == "skip" and "macbook" in out["why"]
    assert not b.comments() and not [a for a in b.calls if "create" in a]
    row = ev(store, "selfheal_occurrence_comment")[-1]
    assert row["ok"] is None and "macbook" in row["skipped"] and row["board"] == "macbook:default"


@pytest.mark.parametrize("board,lands", [("hermes-hub:default", True), ("macbook:default", False)])
def test_incident_comment(store, here, board, lands):
    d = {"fingerprint": "sqlitecorrupt-0123456789abcdef", "symptom": "database disk image is malformed",
         "details": "x", "incident": {"db": "~/.hermes/kanban.db", "host": "hermes-hub", "hour": 1}}
    st = {"status": "filed", "card": "t_abcdef20", "board": board}
    b = SlugBoard()
    out = sh._corruption_comment(d, st, store, b)
    row = ev(store, "selfheal_incident_comment")[-1]
    if lands:
        assert out["why"] == "existing SQLite incident" and row["ok"] is True
        assert b.comments()[0][:4] == ["hermes", "kanban", "--board", "default"]
    else:
        assert out["action"] == "skip" and not b.calls and row["ok"] is None and "macbook" in row["skipped"]


def test_inbox_step_note_local_lands_remote_skipped_once(store, here):
    lane = sh.LaneState(store.root)
    lane.update("agent-0000000001", status="approved", fix_card="t_abcdef31", board="hermes-hub:default")
    lane.update("agent-0000000002", status="approved", fix_card="t_abcdef32", board="macbook:default")
    b = SlugBoard()
    assert sh.sweep_inbox_steps(store, b) == [("agent-0000000001", "t_abcdef31")]
    assert all(a[:4] == ["hermes", "kanban", "--board", "default"] for a in b.calls)
    assert [a[a.index("comment") + 1] for a in b.comments()] == ["t_abcdef31"]
    sh.sweep_inbox_steps(store, b)  # the remote skip is logged once per card, the local note made once
    rows = ev(store, "selfheal_inbox_step_noted")
    assert [(r["card"], r["ok"]) for r in rows] == [("t_abcdef31", True), ("t_abcdef32", None)]
    assert len(b.comments()) == 1
