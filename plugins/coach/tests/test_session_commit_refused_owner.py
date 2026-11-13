"""The session-commit check also runs when every session card was refused as owner (defect desktop-d2cbfde516).

The origin session fixed "@ typeahead breaks on space" 14 min after the complaint (hermes-mods 2718b729, named in its
reply). Its only card was the CI gate for that fix ("hermes-mods suite plugins/session-mentions red on the hub core at
2718b729f643"): the lane adopted it, revalidation rejected it (no subject evidence) and requeued the detection, and
the next pass skipped it as a rejected owner. `session_commit_fix` only ran when the session had no card at all, so
the lane filed an investigation of an already-fixed defect (t_01a1d3fb80d7f18f).
"""
from test_self_heal_adopt_revalidate import Board, events, pending
from test_self_heal_lane import sh, NOW, Runner, HUB, OPEN, ev, store  # noqa: F401 (fixture)
from test_session_commit_mods import COMPLAINT, PANEL_FIX, _defect, _digest, _env, _repo, _transcript

GATE_ID = "t_0000c1ca7e00"


def _gate_card(sha):
    return {"id": GATE_ID, "title": f"hermes-mods suite plugins/ci-gate red on the hub core at {sha[:12]}",
            "body": f"Suite plugins/ci-gate failed on hub core at {sha}. Rerun it and fix any red test.",
            "idempotency_key": None, "created_at": COMPLAINT + 900, "completed_at": None}


def _fixed_session(store, tmp_path, monkeypatch):
    """The defect, a hermes-mods commit named in the complaint's reply, and the session's CI-gate card."""
    mods, sha = _repo(tmp_path, "mods", PANEL_FIX, "desktop-plugins/session-panel/plugin.js")
    _env(monkeypatch, tmp_path, mods=mods)
    d = _defect()
    _digest(tmp_path, d, "…[clipped")
    _transcript(tmp_path, d["session_id"], [
        ("user", COMPLAINT - 0.4, "figure out why it's not showing the block"),
        ("assistant", COMPLAINT + 240, f"The collapsed panel hid the ask. Fixed in hermes-mods `{sha[:8]}`.")])
    gate = _gate_card(sha)
    assert sh.session_owner_refusal(gate, d, d["fingerprint"]) == sh.REFUSE_NO_EVIDENCE
    return d, sha, gate


def _session_board(tmp_path, d, card):
    import sqlite3
    db = sqlite3.connect(tmp_path / "home" / "kanban.db")
    db.executescript("create table tasks(id text, title text, body text, status text, created_at int, session_id text);"
                     "create table kanban_notify_subs(task_id text, chat_id text);")
    db.execute("insert into tasks values(?,?,?,?,?,?)",
               (card["id"], card["title"], card["body"], "ready", int(card["created_at"]), d["session_id"]))
    db.commit()
    db.close()


def _assert_closed_as_fixed(store, d, sha):
    st = sh.LaneState(store.root).get(d["fingerprint"])
    assert st["status"] == "fixed", st
    (fixed,) = ev(store, "selfheal_fixed")
    assert sha[:12] in fixed["note"]
    assert [e["commit"] for e in ev(store, "selfheal_adopted_owner") if e.get("commit")] == [sha]


def test_refused_non_owner_card_does_not_skip_the_commit_check(store, tmp_path, monkeypatch):
    d, sha, gate = _fixed_session(store, tmp_path, monkeypatch)
    _session_board(tmp_path, d, gate)
    r = Runner()
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "skip" and out["adopted"] and out["commit"] == sha
    assert not r.creates(), "an already-fixed defect must not get an investigation card"
    _assert_closed_as_fixed(store, d, sha)


def test_previously_rejected_owner_does_not_skip_the_commit_check(store, tmp_path, monkeypatch):
    """The hub's actual second pass: the gate card is in rejected_owners after revalidation."""
    d, sha, gate = _fixed_session(store, tmp_path, monkeypatch)
    _session_board(tmp_path, d, gate)
    sh.LaneState(store.root).update(d["fingerprint"], status="stale", rejected_owners=[GATE_ID])
    r = Runner()
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "skip" and out["commit"] == sha and not r.creates()
    _assert_closed_as_fixed(store, d, sha)


def test_no_fix_commit_still_files_after_refusing_every_card(store, tmp_path, monkeypatch):
    """Control: with no defect-bound commit, refusing the only card still files an owner."""
    d, sha, gate = _fixed_session(store, tmp_path, monkeypatch)
    _env(monkeypatch, tmp_path)  # no repos: nothing to find
    _session_board(tmp_path, d, gate)
    r = Runner()
    out = sh.file_defect(d, store=store, m="live", runner=r, now=NOW, cfg=HUB, budget_fn=OPEN)
    assert out["action"] == "filed" and len(r.creates()) == 1
    assert ev(store, "selfheal_adopt_skipped") and not ev(store, "selfheal_fixed")


def test_revalidation_rejecting_the_owner_closes_a_session_fixed_defect_instead_of_requeueing(
        store, tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "budget", OPEN)
    d, sha, gate = _fixed_session(store, tmp_path, monkeypatch)
    fp = d["fingerprint"]
    store.log({"event": "selfheal_detect", "mode": "live", "fingerprint": fp, "ts": d["at"],
               **{k: d[k] for k in ("session_id", "source", "component", "symptom")}})
    store.log({"event": "selfheal_adopted_owner", "fingerprint": fp, "card": GATE_ID, "session_id": d["session_id"]})
    sh.LaneState(store.root).update(fp, status="approved", fix_card=GATE_ID, approved_at=COMPLAINT + 1000,
                                    component=d["component"], symptom=d["symptom"], session_id=d["session_id"],
                                    complaint_at=COMPLAINT, cards=[GATE_ID])
    board = Board([gate])
    sh.reconcile(store, runner=board, now=NOW)
    assert not board.creates()
    (rej,) = events(store, "selfheal_adoption_rejected")
    assert rej["card"] == GATE_ID and not rej["requeued"] and rej["fixed_by"] == sha
    assert fp not in pending(store), "a defect the session already fixed is not queued for a fresh owner"
    st = sh.LaneState(store.root).get(fp)
    assert st["status"] == "fixed" and GATE_ID in st["rejected_owners"] and st.get("fix_card") != GATE_ID
    assert sha[:12] in ev(store, "selfheal_fixed")[0]["note"]
