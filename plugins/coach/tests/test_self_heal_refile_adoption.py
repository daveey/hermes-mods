"""A hand re-file never makes the filing card (or its synthetic quote) the defect's owner.

Two false adoptions on 2026-11-12 (t_01a1cfc408527b07), when t_01a1cfa4141139e8 re-filed four fingerprints with
`self_heal.py file --mode live`:

1. via=signature: the re-filing card's body quoted the payload (the identical symptom), so signature matching made
   the still-open re-filing card the owner of agent-4027d5f47f. The filer runs over hub-admin/ssh, where
   $HERMES_KANBAN_TASK is not set, so an env check alone cannot see it: the payload names it (`filed_by`), and a card
   whose body instructs `self_heal.py file` is a filing card, never an investigation.
2. via=complaint_quote: every payload carried the same long synthetic `quote` ("Re-filed by ..."), so the cross-session
   long-quote dedup (agent-ea7ff00170, kept intact here) merged agent-ad52782f01/agent-2348ea4ff7 into another
   session's lane. A `filed_by` payload's quote is the filer's words, not the user's turn: it carries no complaint
   identity.
"""
import json
import sqlite3

from test_self_heal_lane import HUB, NOW, OPEN, Runner, ev, sh, store  # noqa: F401
import pytest

FILER = "t_01a1cfa4141139e8"
REFILE_QUOTE = ("Re-filed by t_01a1c887ce541598: the first filing was closed against an unrelated card of the same "
                "session (false adoption) and never investigated.")
SYMPTOM = ("OperationalError in _execute_write (hermes_state.py): api_content backfill failed for "
           "session=20261028_221818_fdf44b")


def payload(fp, symptom, session, **kw):
    d = {"component": "agent", "symptom": symptom, "source": "backstop", "kind": "hermes_bug", "p_defect": 0.8,
         "session_id": session, "host": "hermes-hub", "fingerprint": fp, "quote": REFILE_QUOTE}
    d.update(kw)
    return d


def refile_body():
    # The shape of t_01a1cfa4141139e8's body: the instruction plus the JSON payload, symptom verbatim.
    return ("Do, on hermes-hub, once per fingerprint:\n\n`python3 ~/.hermes/plugins/coach/self_heal.py file --mode live` "
            "with this JSON on stdin:\n\n" + json.dumps(payload("agent-4027d5f47f", SYMPTOM, "20261028_221818_fdf44b")))


@pytest.fixture(autouse=True)
def _no_worker_env(monkeypatch):
    # The suite may run inside a Kanban worker; the filer env must come only from the tests that set it.
    for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK"):
        monkeypatch.delenv(k, raising=False)


def file(d, store, runner, sig_cards=()):
    return sh.file_defect(d, store=store, m="live", runner=runner, now=NOW, cfg=HUB, budget_fn=OPEN,
                          owner_fn=lambda *a: [], sig_owner_fn=lambda sig: [dict(c) for c in sig_cards])


FILER_CARD = {"id": FILER, "title": "Re-file four self-heal fingerprints", "status": "running",
              "created_at": NOW - 100, "board": None}


# ── case 1: signature adoption of the filer ──
def test_signature_adopts_the_filer_card_without_filed_by(store):
    """The incident as it happened: nothing tells the lane the candidate is the filer."""
    out = file(payload("agent-4027d5f47f", SYMPTOM, "20261028_221818_fdf44b"), store, Runner(), [FILER_CARD])
    assert out.get("via") == "signature" and out["card"] == FILER


def test_signature_never_adopts_the_card_named_by_filed_by(store):
    runner = Runner()
    d = payload("agent-4027d5f47f", SYMPTOM, "20261028_221818_fdf44b", filed_by=FILER)
    out = file(d, store, runner, [FILER_CARD])
    assert out["action"] == "filed", out
    assert len(runner.creates()) == 1
    assert not ev(store, "selfheal_adopted_owner")
    skipped = ev(store, "selfheal_adopt_skipped")
    assert skipped and skipped[-1]["filer_cards"] == [FILER]


def test_signature_still_adopts_a_real_owner_next_to_the_filer(store):
    owner = {**FILER_CARD, "id": "t_0000beef", "title": "api_content backfill failed in _execute_write"}
    d = payload("agent-4027d5f47f", SYMPTOM, "20261028_221818_fdf44b", filed_by=FILER)
    out = file(d, store, Runner(), [FILER_CARD, owner])
    assert out.get("via") == "signature" and out["card"] == "t_0000beef"


def test_kanban_task_env_is_a_filer(store, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_WORKER_TASK", FILER)
    out = file(payload("agent-4027d5f47f", SYMPTOM, "20261028_221818_fdf44b"), store, Runner(), [FILER_CARD])
    assert out["action"] == "filed", out


def _board(home, rows):
    home.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(home / "kanban.db")
    db.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, status TEXT, created_at REAL)")
    db.executemany("INSERT INTO tasks VALUES (?, ?, ?, ?, ?)", rows)
    db.commit()
    db.close()


def test_signature_owned_cards_skips_a_card_that_instructs_filing(tmp_path):
    """Without filed_by (an older filer, a review or audit quoting the payload) the body itself gives it away."""
    _board(tmp_path, [
        (FILER, "Re-file four self-heal fingerprints", refile_body(), "running", NOW - 100),
        ("t_0000beef", "Investigate api_content backfill", f"Symptom: {SYMPTOM}\nRoot cause in _execute_write.",
         "running", NOW - 50),
    ])
    sig = sh.error_signature(payload("agent-4027d5f47f", SYMPTOM, "s"))
    assert [c["id"] for c in sh.signature_owned_cards(sig, home=tmp_path)] == ["t_0000beef"]


# ── case 2: a shared synthetic re-file quote across sessions ──
REFILES = [
    ("agent-4027d5f47f", SYMPTOM, "20261028_221818_fdf44b"),
    ("agent-ad52782f01", "Secret printed to chat; redaction sed syntax failed silently", "20261026_235722_bef6c6"),
    ("agent-2348ea4ff7", "reported failing tests without fixing them", "20261026_235722_bef6c6"),
]


def test_shared_refile_quote_merges_lanes_across_sessions_without_filed_by(store):
    """The incident as it happened: the long synthetic quote is one complaint to the cross-session dedup."""
    runner = Runner()
    outs = [file(payload(*r), store, runner) for r in REFILES]
    assert outs[0]["action"] == "filed"
    assert [o.get("via") for o in outs[1:]] == ["complaint_quote", "complaint_quote"]
    assert len(runner.creates()) == 1


def test_filed_by_refiles_each_get_their_own_card(store):
    runner = Runner()
    outs = [file(payload(*r, filed_by=FILER), store, runner) for r in REFILES]
    assert [o["action"] for o in outs] == ["filed"] * 3, outs
    assert len(runner.creates()) == 3
    assert not ev(store, "selfheal_adopted_owner")
    lane = sh.LaneState(store.root).read()
    assert all(lane[fp].get("complaint_quote_hash") is None for fp, _, _ in REFILES)


def test_filed_by_keeps_an_explicit_complaint_time():
    ident = sh._complaint_identity({"quote": REFILE_QUOTE, "at": NOW, "complaint_at": NOW - 5, "filed_by": FILER})
    assert ident == {"complaint_at": NOW - 5, "complaint_quote_hash": None}


def test_long_quote_dedup_still_holds_for_detector_payloads(store):
    """agent-ea7ff00170 is unchanged: no filed_by, one long quote over sessions, one card."""
    runner = Runner()
    outs = [file(payload(fp, sym, sess, quote=REFILE_QUOTE + " (user words)"), store, runner)
            for fp, sym, sess in REFILES[:2]]
    assert outs[1].get("via") == "complaint_quote"


# ── CLI ──
@pytest.mark.parametrize("argv,env,want", [
    (["file", "--mode", "shadow", "--filed-by", "t_0000aaaa"], {}, "t_0000aaaa"),
    (["file", "--mode", "shadow"], {"HERMES_KANBAN_WORKER_TASK": "t_0000bbbb"}, "t_0000bbbb"),
    (["file", "--mode", "shadow"], {"HERMES_KANBAN_TASK": "t_0000cccc"}, "t_0000cccc"),
    (["file", "--mode", "shadow"], {}, None),
])
def test_cli_file_stamps_filed_by(monkeypatch, capsys, argv, env, want):
    for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    seen = {}
    monkeypatch.setattr(sh, "file_defect", lambda d, m=None: seen.update(d) or {"action": "skip"})
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"symptom": "x"})))
    sh.main(argv)
    assert seen.get("filed_by") == want


def test_cli_payload_filed_by_wins(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_KANBAN_WORKER_TASK", "t_0000bbbb")
    seen = {}
    monkeypatch.setattr(sh, "file_defect", lambda d, m=None: seen.update(d) or {"action": "skip"})
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"symptom": "x", "filed_by": FILER})))
    sh.main(["file", "--mode", "shadow"])
    assert seen["filed_by"] == FILER
