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


# ---- forecasts are not past stamps (hub 7-day backtest of 4df0b983: 64 of 66 hits were forecasts) -------------------
# Each excerpt is a real hub reply, frozen at the time it was sent. A tagged time now needs positive past evidence (a
# past cue in its clause, or a bare stamp like "Red sweep ~21:15 PST:"), so forecasts phrased any way pass.
UTC = ZoneInfo("UTC")
HUB_FORECASTS = [
    # (message, reply time PST)
    ("Sent the notice for t_1: HTTP 429: Subscription quota unavailable; resets at 1794379094.9121644. "
     "Retrying once at 22:38 PT.", (2026, 11, 10, 19, 39, 18)),                                       # msg 1085689 x48
    ("Queued **R-19** replying to [a paper](https://example.com/p). **Pending; auto-posts at 3:42 PM PT unless "
     "vetoed.**", (2026, 11, 6, 15, 14, 34)),                                                          # msg 972928
    ("**R-24 queued\u2014not posted.** Auto-posts at **2:39 PM PT** unless vetoed.",
     (2026, 11, 7, 14, 15, 53)),                                                                       # msg 991001
    ("**R-28 queued\u2014not posted.** Auto-posts at **10:40 AM PT** unless vetoed.",
     (2026, 11, 9, 10, 13, 39)),                                                                       # msg 1018565
    ("**R-33 queued\u2014not posted.** Auto-posts at **10:43 AM PT** unless vetoed in Hermes or BB.",
     (2026, 11, 11, 10, 19, 45)),                                                                      # msg 1139168
    ("**R-26 queued \u2014 not posted.** The 30-minute veto window ends at **4:41 PM PT**.",
     (2026, 11, 8, 16, 13, 27)),                                                                       # msg 1001838
    ("**R-27**, replying to [a post](https://example.com/p). **Pending\u2014not posted.** Veto window ends at "
     "**8:47 AM PT**.", (2026, 11, 9, 8, 23, 23)),                                                     # msg 1015300
    ("Queued **R-35** replying to [a post](https://example.com/p), with the 30-minute veto window ending at "
     "**12:45 PM PT**:", (2026, 11, 11, 12, 20, 7)),                                                   # msg 1154011
    ("At 03:31 PST hermes-sync logged `core: 1 commit(s) behind; batching them \u2026 next in 20m`, so the update "
     "should install around 03:51 PST. Then each gateway has to finish its current work.",
     (2026, 11, 10, 3, 43, 43)),                                                                       # msg 1034427
    ("The worker changed the script to keep that output and deployed it to the MacBook. It checks again at about "
     "9:54 AM PT, and the card closes only after 3 normal scheduled runs.", (2026, 11, 10, 9, 24, 47)),  # msg 1037188
    ("- **Not yet proven:** that an actual renewal happens when the access gets low. The first real renewal should "
     "come around 2:30 PM PT. The card stays open to confirm it.", (2026, 11, 10, 12, 28, 17)),        # msg 1048074
    ("A separate worker started that copy at 15:13 PST. The card checks back at about 18:05 PST tonight to confirm "
     "it landed, then closes.", (2026, 11, 10, 15, 13, 57)),                                           # msg 1059995
    ("That job started at 15:13 PST and was still reporting progress at 15:19.\n\n**What I changed just now:** the "
     "card was set to check back on a timer at 18:05 PST. I've made it wait on the copy job directly, so it closes "
     "as soon as the copy lands rather than at 18:05.", (2026, 11, 10, 15, 20, 27)),                   # msg 1061476
    ("The worker had parked the card with a check on the copy job. The 18:05 PST time was only a fallback deadline "
     "in case that check never fired. When you first asked, I read that deadline and told you the card would "
     "\"check back at about 18:05\". That was wrong: it never needed to wait until 18:05.",
     (2026, 11, 10, 15, 22, 12)),                                                                      # msg 1061659
    ("The first call was refused by the coach `future_clock` guard because it was 05:44 PST and the stamp said "
     "~06:04. The second call, with the same body, succeeded (ok, comment_id 193541) and the comment is now on the "
     "card with the wrong ~06:04 PST time.", (2026, 11, 12, 5, 45, 0)),                                # d_36fb7229535948fd
    ("It will open the card by itself at about 06:12 UTC (10:12 pm PST). Nothing here needs you.",
     (2026, 11, 11, 21, 12, 32)),                                                                      # msg 1209074
]


def test_hub_forecasts_pass():
    for text, when in HUB_FORECASTS:
        assert rc.future_clock(text, now=_at(*when), local_tz=UTC) is None, text
        assert rc.future_clock(text, now=_at(*when), local_tz=LA) is None, text


def test_untagged_time_on_a_utc_host_passes():
    # msg 1013527, said at 15:41Z on the UTC hub: an earlier day's 17:28 UTC, not a future stamp
    text = ("The quote granted access to a blocked card; that card (`t_01a194e0d9b6e020`) was unblocked and finished "
            "at 17:28. No detector record exists for the turn.")
    assert rc.future_clock(text, now=_at(2026, 11, 9, 7, 41, 12), local_tz=UTC) is None


def test_untagged_time_more_than_an_hour_ahead_is_an_earlier_days():
    assert rc.future_clock("It finished at 21:58.", now=_at(2026, 11, 11, 21, 45), local_tz=LA)
    assert rc.future_clock("It finished at 23:00.", now=_at(2026, 11, 11, 21, 45), local_tz=LA) is None


def test_more_forecast_phrasings_pass():
    for text in ("The sweep should run at 21:30 PT.",
                 "It would land around 21:40 PT.",
                 "Next sweep ~21:30 PST:",
                 "The card is set to check back at 21:30 PT.",
                 "Restarting the gateway at 21:10 PT.",
                 "The 21:30 PT time is only a fallback deadline.",
                 "That 21:30 PT stamp was incorrect.",
                 "The comment carries a mistaken 21:30 PT time."):
        assert rc.future_clock(text, now=NOW) is None, text


def test_past_cues_still_fire():
    for text in ("The sweep ran at 21:15 PT.",
                 "Merged at 21:20 PT; CI was green.",
                 "Deployed to the hub at 21:10 PST.",
                 "The last check was at 21:30 PT.",
                 "At 21:20 PT the sweep finished.",
                 "Red sweep ~21:15 PST: still blocked on the hub token.",
                 "Red sweep 21:15 PST: still blocked on the hub token.",
                 "Swept 9 red cards (21:15 PST).",
                 "Checked 06:14 PT."):
        when = _at(2026, 11, 12, 5, 44, 33) if "06:14" in text else NOW   # the probe draft d_3148df71a8544153
        assert rc.future_clock(text, now=when), text


def test_dst_switch_back_references_and_that_day_pass():
    # hub 30-day backtest: replies on Nov 1 (PST) describing Oct 31 events in PDT, and "since 15:09 PT that day"
    nov1 = _at(2026, 11, 1, 7, 56, 11)
    assert rc.future_clock("**What happened:** on 10-31 at 12:27 PDT, the worker session crashed. All three recovery "
                           "attempts (12:37\u201312:40 PDT) failed this way.", now=nov1) is None       # msg 571964
    assert rc.future_clock("Both crashes (runs 11789 and 11790, 31 Oct at 16:27 PDT) died before the first turn.",
                           now=_at(2026, 11, 1, 7, 54, 13)) is None                                      # msg 571576
    assert rc.future_clock("The skill has recorded the move since 15:09 PT that day.",
                           now=_at(2026, 10, 29, 14, 28, 48)) is None                                    # msg 350571
    # an undated PDT label in November still fires (the incident)
    assert rc.future_clock("All three recovery attempts (12:37\u201312:40 PDT) failed this way.", now=nov1)
