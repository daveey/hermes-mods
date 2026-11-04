"""signals `corruption` collector: core's unconfirmed-corruption WARNING records (selfheal gateway-d7a592a91c).

A structural SQLITE_CORRUPT that a scoped read-only quick_check did not confirm is replayed once instead of
latching; core logs it with no traceback. The collector must file only genuine `hermes_state` log records for a live
db, never text a card, a test or a tool echoed, and the lane must keep ONE Kanban owner per episode (no Inbox).
The four sentences below are copied from hermes_state.py; the read pair is pinned to the producer by a core test
(tests/hermes_state/test_storage_corruption_scoped_confirm.py::test_unconfirmed_warning_sentences_are_the_coach_contract).
"""
import time
from pathlib import Path

import pytest

from test_signals import NOW, sg
from test_self_heal_lane import HUB, OPEN, Runner, core, sh

HOME = Path("/home/ubuntu/.hermes")
DB = f"{HOME}/state.db"
DETAIL = "database disk image is malformed [sqlite=SQLITE_CORRUPT(11)]"

READ_FIRST = (f"state.db {DB}: a read raised structural corruption ({DETAIL}), NOT confirmed (confirm=clean; "
              f"unconfirmed this process: 1); replaying it once before latching.")
WRITE_FIRST = (f"state.db {DB}: transcript.append raised structural corruption ({DETAIL}) at stage=callback, NOT "
               f"confirmed (confirm=clean; unconfirmed this process: 2); the transaction rolled back, replaying it "
               f"once on the same connection before quarantining.")
READ_GONE = (f"state.db {DB}: read corruption ({DETAIL}) did not recur on replay; storage is NOT latched "
             f"(unconfirmed corruption).")
WRITE_GONE = (f"state.db {DB}: structural corruption reported by transcript.append ({DETAIL}) did not recur on the "
              f"same-connection replay; the write landed and storage is NOT latched (unconfirmed corruption).")
GENUINE = (READ_FIRST, WRITE_FIRST, READ_GONE, WRITE_GONE)


def rec(msg, level="WARNING", logger="hermes_state", at=NOW, session="[local-abc] "):
    return f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(at))},123 {level} {session}{logger}: {msg}"


def signals(lines, root=HOME, prof="default", host="hub"):
    return sg.corruption_signals(lines, root, prof, NOW + 60, host)


def make_home(tmp_path, lines, prof=None):
    root = tmp_path / ".hermes"
    h = root if prof is None else root / "profiles" / prof
    (h / "logs").mkdir(parents=True)
    (h / "logs" / "errors.log").write_text("\n".join(lines) + "\n")
    return root


# ── genuine records ──

@pytest.mark.parametrize("msg", GENUINE)
def test_each_core_sentence_is_a_signal(msg):
    [s] = signals([rec(msg)])
    assert s["kind"] == "corruption" and s["component"] == "agent" and s["profile"] == "default"
    assert "(unconfirmed corruption)" in s["symptom"] and "~/.hermes/state.db" in s["symptom"]
    assert "never damaged" in s["details"]  # a later clean check does not prove it was never damaged


def test_error_level_record_counts_and_session_tag_is_optional():
    assert len(signals([rec(READ_FIRST, level="ERROR", session="")])) == 1


def test_log_file_cursor_and_profile_db(tmp_path):
    root = tmp_path / ".hermes"
    msg = READ_FIRST.replace(DB, f"{root}/profiles/coach/state.db")
    make_home(tmp_path, [rec(msg)], prof="coach")
    cur = {}
    [s] = sg.collect_corruption(root, cur, NOW + 60, "hub")
    assert s["profile"] == "coach" and s["kind"] == "corruption"
    assert sg.collect_corruption(root, cur, NOW + 120, "hub") == []  # cursor: each record is seen once


def test_collect_entry_point_keeps_one_signal_per_db_per_pass(tmp_path):
    root = tmp_path / ".hermes"
    make_home(tmp_path, [rec(m.replace(DB, f"{root}/state.db"), at=NOW + i) for i, m in enumerate(GENUINE)])
    out = [s for s in sg.collect(root, {}, "hub", NOW + 60) if s["kind"] == "corruption"]
    assert len(out) == 1 and out[0]["host"] == "hub" and out[0]["count"] == 4
    assert "corruption" in sg.KINDS  # hub ingest accepts forwarded corruption signals


def test_old_records_past_the_backfill_are_ignored():
    assert sg.corruption_signals([rec(READ_FIRST, at=NOW - sg.BACKFILL_S - 60)], HOME, "default", NOW, "hub") == []


# ── no echoed card / test / tool text ──

def test_echoed_text_never_matches():
    lines = [
        rec(READ_FIRST, logger="agent.tool_executor"),                # a tool printed it
        rec(READ_FIRST, logger="hermes_cli.kanban"),                  # another logger (a card body, a note)
        rec(READ_FIRST, level="INFO"),                                # not a warning
        rec(READ_FIRST, level="DEBUG"),
        READ_FIRST,                                                   # no record head (traceback / card / test text)
        "    " + rec(READ_FIRST),                                     # indented continuation line
        f"AssertionError: assert {READ_FIRST!r} in caplog.text",      # a test failure echoing it
        rec(f"Tool terminal returned: {rec(READ_FIRST)}", logger="agent.tool_executor"),
        rec(f"quoted: {READ_FIRST}"),                                 # right logger, message does not start with it
        rec(READ_FIRST.replace("NOT confirmed", "confirmed")),        # a confirmed one latches: not this case
        rec(f"state.db {DB}: structurally corrupt; session storage is reported as corrupt"),
        rec(READ_GONE + " extra"),                                    # not core's sentence
        rec(READ_GONE.replace("hermes", "x")[:-1]),                   # truncated sentence
    ]
    assert signals(lines) == []


def test_card_text_in_a_defect_never_joins_a_corruption_incident():
    d = {"component": "agent", "kind": "errors", "symptom": "pytest failed: storage is NOT latched "
         "(unconfirmed corruption).", "details": DB, "host": "hub", "at": NOW}
    assert sh.corruption_incident(d, NOW) is None
    assert sh.corruption_incident({**d, "kind": "corruption"}, NOW)["db"] == "~/.hermes/state.db"


# ── fixture / scratch exclusion ──

@pytest.mark.parametrize("db", ["/tmp/pytest-of-ubuntu/pytest-56/test_x0/state.db",
                                "/home/ubuntu/.hermes/cache/scratch/hh/state.db",
                                "/home/ubuntu/.hermes/cache/core-update/stage/state.db",
                                "/home/other/.hermes/state.db",
                                "relative/state.db"])
def test_scratch_and_foreign_dbs_are_excluded(db):
    assert signals([rec(m.replace(DB, db)) for m in GENUINE]) == []


# ── one owner per episode, no Inbox ──

def _file(store, runner, s, now):
    return sh.file_defect(sg.to_defect({**s, "host": "hub"}), store=store, runner=runner, m="live", cfg=HUB,
                          budget_fn=OPEN, now=now)


def test_episode_is_one_kanban_card_then_comments_and_never_inbox(tmp_path):
    store = core.Store(tmp_path / "coach")
    [first] = signals([rec(READ_FIRST)])
    runner = Runner()
    assert _file(store, runner, first, NOW + 60)["action"] == "filed"
    assert len(runner.creates()) == 1
    # Later passes in the same episode (host + db + UTC hour): comment on the open owner, no second card.
    later = {**first, "at": first["at"] + 120}
    r2 = _file(store, runner, later, NOW + 300)
    assert len(runner.creates()) == 1
    assert r2["action"] == "skip" and r2["card"] == "t_00000001"
    assert any("comment" in a and "t_00000001" in a for a, _ in runner.calls)
    assert not [a for a, _ in runner.calls if "inbox" in a]  # Kanban owner only
    assert "--assignee" in runner.creates()[0] or "create" in runner.creates()[0]


def test_episode_identity_is_host_db_hour():
    [s] = signals([rec(READ_FIRST)])
    d = sg.to_defect({**s, "host": "hub"})
    inc = sh.corruption_incident(d, NOW)
    assert inc == {"db": "~/.hermes/state.db", "host": "hub", "hour": int(s["at"]) // 3600}
    assert sh.corruption_incident({**d, "at": s["at"] + 3600}, NOW) != inc
    assert sh.corruption_incident({**d, "host": "mini"}, NOW) != inc
    [other] = signals([rec(READ_FIRST.replace(DB, f"{HOME}/profiles/coach/state.db"))])
    assert sh.corruption_incident(sg.to_defect({**other, "host": "hub"}), NOW) != inc
