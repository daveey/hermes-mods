"""future_clock (self-heal agent-748d257889): a reply or a durable card text that stamps a past-tense Pacific time
which has not happened yet is caught deterministically, before the judge.

Incident (MacBook, 11-11 at 20:59:32 PST): a red-sweep report said "(Checked 21:05–21:40 PT.)" and, at 20:50 PT,
card comments said "Red sweep ~21:15 PST". The model converted UTC tool stamps with the summer offset (UTC-7) after
DST had ended (UTC-8) and never read the clock. It recurred at 21:45 ("checked 21:37–21:58").
"""
import datetime
import importlib.util
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t_reply_check_future_clock", HERE / "reply_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

LA = ZoneInfo("America/Los_Angeles")


def _at(y, mo, d, h, mi, s=0):
    return datetime.datetime(y, mo, d, h, mi, s, tzinfo=LA).timestamp()


NOW = _at(2026, 11, 11, 20, 59, 32)        # the incident's report time, PST
LATER = _at(2026, 11, 11, 21, 59, 0)       # after every claimed time
SUMMER = _at(2026, 7, 15, 20, 59, 32)      # PDT in force

REPORT = ("Swept 9 red cards: 6 unblocked, 3 still need you (listed above). (Checked 21:05\u201321:40 PT.)")
COMMENT = "Red sweep ~21:15 PST: still blocked on the hub token, re-filed the ask."
RECUR = "All three PRs are green; checked 21:37\u201321:58 and nothing else moved."


def _no_judge(system, prompt):
    raise AssertionError("future_clock must not need the LLM judge")


def _quiet_judge(system, prompt):
    return '{"handoff":0,"confirm":0,"unwatched":0,"report_only":0,"status_only":0,"evidence":""}'


def test_incident_report_fires_before_the_judge():
    hit = rc.check(REPORT, "sweep the red cards", [], _no_judge, only=["future_clock"], now=NOW)
    assert hit and hit["rule"] == "future_clock" and hit["p"] == 1.0
    assert "21:40" in hit["evidence"]
    # the nudge states the real local time and offset and how to convert
    assert "20:59 PST" in hit["nudge"] and "UTC\u22128" in hit["nudge"]
    assert "TZ=America/Los_Angeles date" in hit["nudge"]


def test_incident_fires_in_the_default_rule_set():
    assert "future_clock" in rc.RULES and "future_clock" in rc.DEFAULT_RULES
    assert "future_clock" in rc.settings()["rules"]
    hit = rc.check(REPORT, "sweep the red cards", [], _quiet_judge, now=NOW)
    assert hit and hit["rule"] == "future_clock"


def test_comment_stamp_fires():
    assert rc.future_clock(COMMENT, now=NOW)
    assert "21:15" in rc.future_clock(COMMENT, now=NOW)


def test_untagged_time_after_checked_fires_in_the_hosts_zone():
    assert rc.future_clock(RECUR, now=_at(2026, 11, 11, 21, 45), local_tz=LA)
    # same text once those times have passed
    assert rc.future_clock(RECUR, now=LATER, local_tz=LA) is None


def test_same_texts_later_pass():
    later = _at(2026, 11, 11, 21, 45)
    assert rc.future_clock(REPORT, now=later) is None
    assert rc.future_clock(COMMENT, now=later) is None


def test_within_grace_passes():
    assert rc.future_clock("Checked 21:02 PT.", now=NOW) is None
    assert rc.future_clock("Checked 21:03 PT.", now=NOW)


def test_forecasts_pass():
    for text in ("The quota brake lifts by 22:16 PT.",
                 "Workers run between 09:00 and 21:00 PT.",
                 "The window opens from 09:00 PT tomorrow.",
                 "I'll recheck at 21:30 PT.",
                 "ETA 21:40 PT for the full suite.",
                 "Expected to land around 22:00 PT.",
                 "It stays parked until 23:00 PT.",
                 "The card wakes before 22:00 PT."):
        assert rc.future_clock(text, now=NOW) is None, text


def test_code_quotes_and_other_days_pass():
    for text in ("Ran `TZ=America/Los_Angeles date` and it printed `21:40 PST`.",
                 "> Checked 21:05\u201321:40 PT.",
                 'My last report said "Checked 21:05\u201321:40 PT." and that was wrong.',
                 "Checked 21:40 PT on Monday.",
                 "Checked 21:40 PT on 11-09.",
                 "Checked 21:40 PT yesterday.",
                 "Checked 04:42 UTC.",
                 "Checked 21:40 PT on Nov 10."):
        assert rc.future_clock(text, now=NOW) is None, text


def test_past_times_pass():
    assert rc.future_clock("(Checked 20:30\u201320:58 PT.)", now=NOW) is None
    assert rc.future_clock("Checked 09:05 PT.", now=NOW) is None


def test_just_after_midnight_yesterdays_evening_passes():
    assert rc.future_clock("Checked 23:55 PT.", now=_at(2026, 11, 12, 0, 10)) is None


def test_wrong_dst_label_fires():
    hit = rc.future_clock("Checked 20:40 PDT.", now=NOW)
    assert hit and "PDT" in hit
    assert rc.future_clock("Checked 20:40 PST.", now=SUMMER)
    assert rc.future_clock("Checked 20:40 PDT.", now=SUMMER) is None
    assert rc.future_clock("Checked 20:40 PST.", now=NOW) is None


def test_nudge_names_the_wrong_label():
    nudge = rc.future_clock_nudge("20:40 PDT", now=NOW)
    assert "PST" in nudge and "UTC\u22128" in nudge


def test_meta_text_naming_the_rule_passes():
    assert rc.future_clock("future_clock flagged 'Checked 21:40 PT.'", now=NOW) is None


def test_durable_text_reads_card_tools():
    assert "21:15" in rc.durable_text("kanban_comment", {"task_id": "t_1", "body": COMMENT})
    assert "21:40" in rc.durable_text("kanban_complete", {"summary": REPORT})
    assert "21:40" in rc.durable_text("kanban_block", {"reason": "x", "human_request": {"evidence": REPORT}})
    assert "21:40" in rc.durable_text("kanban_create", {"title": "t", "body": REPORT})
    wrapped = rc.durable_text("tool_call", {"calls": [{"name": "kanban_comment", "arguments": {"body": COMMENT}}]})
    assert "21:15" in wrapped
    assert rc.durable_text("terminal", {"command": COMMENT}) == ""


def test_backtest_registers_the_rule():
    assert "future_clock" in rc.BACKTEST_RULES


def test_backtest_judges_each_reply_at_its_own_time(tmp_path):
    # replies from 2 days ago: one future-dated when written, one fine when written. Judged against "now" instead,
    # the outcome would depend on the hour the backtest runs.
    import json
    import sqlite3
    d = datetime.datetime.now(LA) - datetime.timedelta(days=2)
    base = d.replace(hour=12, minute=0, second=0, microsecond=0).timestamp()  # noon: no midnight wrap
    db = tmp_path / "state.db"
    c = sqlite3.connect(db)
    c.execute("create table messages (id integer primary key, session_id text, role text, content text, timestamp real)")
    def stamp(ts, mins):
        return datetime.datetime.fromtimestamp(ts + mins * 60, LA).strftime("%H:%M")
    bad, good = base, base + 3600
    c.executemany("insert into messages (session_id, role, content, timestamp) values (?,?,?,?)", [
        ("s_bad", "assistant", f"Swept the board. (Checked {stamp(bad, 20)} PT.)", bad),
        ("s_ok", "assistant", f"Swept the board. (Checked {stamp(good, -20)} PT.)", good)])
    c.commit()
    c.close()
    drafts = tmp_path / "drafts.jsonl"
    drafts.write_text(json.dumps({"draft_id": "d_1", "session_id": "s_draft", "ts": bad,
                                  "draft": f"Checked {stamp(bad, 30)} PT."}) + "\n")
    hits = rc.backtest("future_clock", 7, db=db, drafts=drafts)
    assert sorted(h[1] for h in hits) == ["s_bad", "s_draft"]


# ---- pre_tool_call: a future-dated stamp is refused once before it lands on a durable card --------------------------
def _hooks(tmp_path, monkeypatch):
    import json  # noqa: F401
    s = importlib.util.spec_from_file_location("coach_init_fc", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    for k in ("COACH_REPLY_RULES", "COACH_BLOCK_TIME"):
        monkeypatch.delenv(k, raising=False)
    rcm = mod._load("reply_check")
    monkeypatch.setattr(rcm, "_wall_now", lambda: NOW)
    monkeypatch.setattr(rcm, "aux_judge", lambda s, p: _quiet_judge(s, p))
    monkeypatch.setattr(mod._load("second_opinion"), "_config_settings", lambda: {})
    store = mod._load("core").Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    return hooks


def _events(tmp_path):
    import json
    p = tmp_path / "coach" / "log.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def test_comment_with_future_stamp_is_refused_once(tmp_path, monkeypatch):
    hooks = _hooks(tmp_path, monkeypatch)
    args = {"task_id": "t_x", "body": COMMENT}
    got = hooks["pre_tool_call"](tool_name="kanban_comment", args=args, session_id="s1")
    assert got and got["action"] == "block" and "future_clock" in got["message"] and "20:59 PST" in got["message"]
    # the same call again goes through: never a loop
    assert hooks["pre_tool_call"](tool_name="kanban_comment", args=args, session_id="s1") is None
    ev = [e for e in _events(tmp_path) if e.get("rule") == "future_clock"]
    assert [e["event"] for e in ev] == ["agent_block", "future_clock_repeat"]


def test_wrapped_and_clean_card_calls(tmp_path, monkeypatch):
    hooks = _hooks(tmp_path, monkeypatch)
    wrapped = {"calls": [{"name": "kanban_complete", "arguments": {"summary": REPORT}}]}
    assert (hooks["pre_tool_call"](tool_name="tool_call", args=wrapped, session_id="s2") or {}).get("action") == "block"
    fine = {"task_id": "t_x", "body": "Red sweep at 20:50 PST: still blocked on the hub token."}
    assert hooks["pre_tool_call"](tool_name="kanban_comment", args=fine, session_id="s3") is None


def test_rule_off_lets_the_comment_through(tmp_path, monkeypatch):
    hooks = _hooks(tmp_path, monkeypatch)
    monkeypatch.setenv("COACH_REPLY_RULES", "handoff")
    assert hooks["pre_tool_call"](tool_name="kanban_comment", args={"body": COMMENT}, session_id="s4") is None


def test_a_time_far_ahead_is_an_earlier_days():
    # "since about 4:28pm PT" said at 05:30 means yesterday afternoon (a false hit in the first suite run)
    text = "Separately, since about 4:28pm PT the hub's task board database has been failing its integrity checks."
    assert rc.future_clock(text, now=_at(2026, 11, 12, 5, 30)) is None    # 11 h ahead: yesterday's
    assert rc.future_clock(text, now=_at(2026, 11, 12, 16, 0))            # 28 min ahead: fires
    assert rc.future_clock(text, now=_at(2026, 11, 12, 16, 30)) is None   # past
    assert rc.future_clock("Checked 23:40 PT.", now=_at(2026, 11, 12, 20, 41))          # 2 h 59 m ahead: fires
    assert rc.future_clock("Checked 23:40 PT.", now=_at(2026, 11, 12, 20, 39)) is None  # 3 h 1 m: earlier day


def test_clock_pin_env(monkeypatch):
    monkeypatch.setenv("COACH_CLOCK_EPOCH", str(NOW))
    assert rc._wall_now() == NOW
    assert rc.future_clock(REPORT)
