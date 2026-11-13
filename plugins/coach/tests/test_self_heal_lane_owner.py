"""Every `hermes kanban` call on a lane's owner card resolves its board through one helper, lane_owner.

Digest-sourced lanes hold '<gateway>:<board>' (live 11-13: 53 hub lanes on 'hermes-hub:default', 11 on
'macbook:default'). `hermes kanban --board hermes-hub:default ...` exits 2 (invalid slug). The gateway label is the
session's origin host, not where the card lives: 5 of 17 cards in the hub's macbook:/mini: lanes were on the hub's own
board. So the board part is shown locally first; a local card is read and written locally whatever the label says,
this host's own gateway always uses the local board, and only another gateway's card that is not here is skipped with
a logged reason. The read paths (owner text for revalidation, card_lookup) and the write paths (occurrence comment,
SQLite incident comment, inbox-step note) share it.
"""
import json
from types import SimpleNamespace

import pytest

from test_self_heal_lane import HUB, NOW, OPEN, OLD_STEP, core, defect, ev, sh, store  # noqa: F401 (fixture)

REMOTE_HERE = "t_abcdef11"  # in a macbook: lane, but on this host's board
BASE = ["hermes", "kanban"]


class SlugBoard:
    """Fake `hermes kanban` that enforces the real CLI's slug check: a ':' in `--board` exits 2. `show --json` answers
    only for the cards on this host's board (`local`; "no such task" otherwise; every card when local is None), with
    an open card whose body still carries the retired inbox step; comment succeeds. `broken` = every call fails."""

    def __init__(self, local=None, broken=False):
        self.calls = []
        self.local = None if local is None else set(local)
        self.broken = broken

    def __call__(self, argv, capture_output=True, text=True, input=None, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        if self.broken:
            return SimpleNamespace(returncode=1, stdout="", stderr="disk I/O error")
        if "--board" in argv and ":" in argv[argv.index("--board") + 1]:
            return SimpleNamespace(returncode=2, stdout="", stderr="kanban: error: invalid board slug")
        if "show" in argv:
            tid = argv[argv.index("show") + 1]
            if self.local is not None and tid not in self.local:
                return SimpleNamespace(returncode=1, stdout="", stderr=f"no such task: {tid}")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"task": {
                "id": tid, "title": "Self-heal FIX", "status": "running", "body": "Self-heal FIX\n" + OLD_STEP}}),
                stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def comments(self):
        return [a for a in self.calls if "comment" in a]

    def shows(self):
        return [a for a in self.calls if "show" in a]


@pytest.fixture
def here(monkeypatch):
    """This host is the hub: digest host 'hermes-hub', Board gateway 'hub'."""
    monkeypatch.setattr(sh, "host_name", lambda: "hermes-hub")
    monkeypatch.setattr(sh._placement_evidence, "home_machine", lambda: "hub")


def test_plain_slug_and_no_board_use_the_local_board_without_a_show(here):
    b = SlugBoard(local=[])
    assert sh.lane_owner("t_x", None, b)["argv"] == BASE
    assert sh.lane_owner("t_x", "bb", b)["argv"] == BASE + ["--board", "bb"]
    assert not b.calls  # nothing to resolve: the caller shows or writes


def test_local_hit_for_a_remote_labelled_lane(here):
    b = SlugBoard(local=[REMOTE_HERE])
    own = sh.lane_owner(REMOTE_HERE, "macbook:default", b)
    assert own["argv"] == BASE + ["--board", "default"] and own["task"]["id"] == REMOTE_HERE and own["shown"]
    assert b.calls == [BASE + ["--board", "default", "show", REMOTE_HERE, "--json"]]
    assert sh.lane_owner(REMOTE_HERE, "mini:", b)["argv"] == BASE  # no board part: the default board


def test_true_remote_miss_is_skipped_with_a_reason(here):
    own = sh.lane_owner("t_abcdef99", "macbook:default", SlugBoard(local=[]))
    assert own["argv"] is None and own["how"] == "no_such_task"
    assert "macbook" in own["why"] and "t_abcdef99" in own["why"] and "not on this host's default board" in own["why"]
    # an unreadable local board is skipped too, and the reason says it was unreadable, not absent
    own = sh.lane_owner("t_abcdef99", "macbook:default", SlugBoard(broken=True))
    assert own["argv"] is None and own["how"] == "unreadable" and "unreadable" in own["why"]


def test_this_hosts_own_gateway_uses_the_local_board_even_on_a_miss(here):
    for label in ("hermes-hub:default", "Hermes-Hub:default", "hub:default"):
        own = sh.lane_owner("t_abcdef99", label, SlugBoard(local=[]))
        assert own["argv"] == BASE + ["--board", "default"] and own["task"] is None and own["how"] == "no_such_task"


def test_unreadable_board_identity_still_knows_the_host(monkeypatch):
    monkeypatch.setattr(sh, "host_name", lambda: "hermes-hub")

    def boom():
        raise sh._placement_evidence.HomePinUnresolved("no identity")
    monkeypatch.setattr(sh._placement_evidence, "home_machine", boom)
    assert sh.lane_owner("t_x", "hermes-hub:default", SlugBoard(local=[]))["argv"] == BASE + ["--board", "default"]
    assert sh.lane_owner("t_x", "hub:default", SlugBoard(local=[]))["argv"] is None


def _occurrence():
    d = defect("gateway", "gateway restarted into a live gateway", source="signal", quote="", session_id=None,
               comment_open_card=True)
    d["fingerprint"] = "gateway-cc28b19bca"
    return d


def _lane(store, board):
    sh.LaneState(store.root).update("gateway-cc28b19bca", status="approved", card="t_abcdef10", fix_card=REMOTE_HERE,
                                  component="gateway", symptom="restart storm", board=board)


def _file(d, store, runner):
    return sh.file_defect(d, store=store, m="live", runner=runner, now=NOW, cfg=HUB, budget_fn=OPEN,
                          owner_fn=lambda *a: [], sig_owner_fn=lambda *a: [])


@pytest.mark.parametrize("board,local,prefix", [
    ("hermes-hub:default", None, ["--board", "default"]),
    ("macbook:default", [REMOTE_HERE], ["--board", "default"]),  # local hit for a remote-labelled lane
    ("default", None, ["--board", "default"]),                   # plain slug
    (None, None, []),                                            # no board
])
def test_occurrence_comment_lands_locally(store, here, board, local, prefix):
    _lane(store, board)
    b = SlugBoard(local=local)
    out = _file(_occurrence(), store, b)
    assert out["action"] == "skip" and out["card"] == REMOTE_HERE
    (c,) = b.comments()
    assert c[:c.index("comment")] == BASE + prefix and c[c.index("comment") + 1] == REMOTE_HERE
    assert ev(store, "selfheal_occurrence_comment")[-1]["ok"] is True


def test_occurrence_comment_on_a_true_remote_card_is_skipped_and_logged(store, here):
    _lane(store, "macbook:default")
    b = SlugBoard(local=[])
    out = _file(_occurrence(), store, b)
    assert out["action"] == "skip" and "macbook" in out["why"]
    assert not b.comments() and not [a for a in b.calls if "create" in a]
    row = ev(store, "selfheal_occurrence_comment")[-1]
    assert row["ok"] is None and "macbook" in row["skipped"] and row["board"] == "macbook:default"


@pytest.mark.parametrize("board,local,lands", [("hermes-hub:default", None, True), ("macbook:default", [], False),
                                               ("macbook:default", ["t_abcdef20"], True)])
def test_incident_comment(store, here, board, local, lands):
    d = {"fingerprint": "sqlitecorrupt-0123456789abcdef", "symptom": "database disk image is malformed",
         "details": "x", "incident": {"db": "~/.hermes/kanban.db", "host": "hermes-hub", "hour": 1}}
    st = {"status": "filed", "card": "t_abcdef20", "board": board}
    b = SlugBoard(local=local)
    out = sh._corruption_comment(d, st, store, b)
    row = ev(store, "selfheal_incident_comment")[-1]
    if lands:
        assert out["why"] == "existing SQLite incident" and row["ok"] is True
        assert b.comments()[0][:4] == ["hermes", "kanban", "--board", "default"]
    else:
        assert out["action"] == "skip" and not b.comments() and row["ok"] is None and "macbook" in row["skipped"]


def test_inbox_step_note_local_lands_remote_skipped_once(store, here):
    lane = sh.LaneState(store.root)
    lane.update("agent-0000000001", status="approved", fix_card="t_abcdef31", board="hermes-hub:default")
    lane.update("agent-0000000002", status="approved", fix_card="t_abcdef32", board="macbook:default")
    lane.update("agent-0000000003", status="approved", fix_card="t_abcdef33", board="mini:default")
    lane.update("agent-0000000004", status="approved", fix_card="t_abcdef34", board="bb")
    lane.update("agent-0000000005", status="approved", fix_card="t_abcdef35")
    b = SlugBoard(local=["t_abcdef31", "t_abcdef33", "t_abcdef34", "t_abcdef35"])
    assert sorted(sh.sweep_inbox_steps(store, b)) == [
        ("agent-0000000001", "t_abcdef31"), ("agent-0000000003", "t_abcdef33"),
        ("agent-0000000004", "t_abcdef34"), ("agent-0000000005", "t_abcdef35")]
    assert not [a for a in b.calls if "--board" in a and ":" in a[a.index("--board") + 1]]
    noted = {a[a.index("comment") + 1]: a[:a.index("comment")] for a in b.comments()}
    assert noted == {"t_abcdef31": BASE + ["--board", "default"], "t_abcdef33": BASE + ["--board", "default"],
                     "t_abcdef34": BASE + ["--board", "bb"], "t_abcdef35": BASE}
    # one show per card: lane_owner's show of a digest-labelled card is reused, not repeated
    assert sorted(a[a.index("show") + 1] for a in b.shows()) == ["t_abcdef31", "t_abcdef32", "t_abcdef33",
                                                                 "t_abcdef34", "t_abcdef35"]
    sh.sweep_inbox_steps(store, b)  # the remote skip is logged once per card, the local notes made once
    rows = ev(store, "selfheal_inbox_step_noted")
    assert sorted((r["card"], r["ok"]) for r in rows) == [("t_abcdef31", True), ("t_abcdef32", None),
                                                          ("t_abcdef33", True), ("t_abcdef34", True),
                                                          ("t_abcdef35", True)]
    assert len(b.comments()) == 4


# ── read paths share the helper ──
def test_owner_card_text_resolves_through_lane_owner(here):
    b = SlugBoard(local=[REMOTE_HERE])
    assert sh._owner_card_text(REMOTE_HERE, "macbook:default", b)["title"] == "Self-heal FIX"
    assert b.calls == [BASE + ["--board", "default", "show", REMOTE_HERE, "--json"]]  # one show, reused
    assert sh._owner_card_text("t_abcdef99", "macbook:default", SlugBoard(local=[])) is None  # true remote miss
    b = SlugBoard(local=["t_abcdef99"])
    assert sh._owner_card_text("t_abcdef99", "bb", b)["title"] == "Self-heal FIX"
    assert sh._owner_card_text("t_abcdef99", None, b)["title"] == "Self-heal FIX"
    assert [a[:a.index("show")] for a in b.calls] == [BASE + ["--board", "bb"], BASE]


def test_card_lookup_answers_from_the_local_board_for_a_remote_labelled_lane(here, tmp_path):
    b = SlugBoard(local=[REMOTE_HERE])
    got = sh.card_lookup(REMOTE_HERE, "macbook:default", b, home=tmp_path, hub_db=tmp_path / "absent.db", now=NOW)
    assert (got["status"], got["authority"], got["board"]) == ("running", "board", "default")
    assert not [a for a in b.calls if "macbook:default" in a]
    # a true remote miss still goes to that gateway's replica (none here: unknown, never "missing")
    got = sh.card_lookup("t_abcdef99", "macbook:default", SlugBoard(local=[]), home=tmp_path,
                         hub_db=tmp_path / "absent.db", now=NOW)
    assert got["status"] is None and got["gateway"] == "macbook"
