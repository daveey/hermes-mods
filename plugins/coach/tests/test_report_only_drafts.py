"""report_only audit trail + the scheduled-wait exemption (kanban t_01a184ac93d31979, from audit t_01a17f79ade9cbdd).

1. Every reply_nudge / report_only_confirm event links (draft_id) to the exact judged draft in coach/drafts.jsonl,
   which survives later turns; the store is redacted and capped by size and age.
2. A pure status question answered with a legitimately scheduled wait never fires report_only; the existing positives
   (failed soak "Desktop app is still timing out", failing tests left in the queue) still do.
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_drafts_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = _mod("reply_check")
dr = _mod("drafts")

FIXTURES = json.loads((HERE / "tests" / "fixtures" / "report_only_scheduled_wait.json").read_text())


def _fx(fid):
    return next(r for r in FIXTURES if r["id"] == fid)


def _verdict(p):
    return {"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": p, "evidence": "screen"}


def _judge(p):
    calls = []

    def llm(system, prompt):
        calls.append(prompt)
        return json.dumps(_verdict(p))
    return llm, calls


# ── 2. scheduled-wait exemption ──────────────────────────────────────────────────────────────────────────────────────
def test_scheduled_wait_fixtures_fire_only_where_expected():
    ths = rc.settings()["thresholds"]
    for f in FIXTURES:
        if f["expect"]:
            assert "report_only" in rc.prefilter(f["reply"]), f["id"]  # every positive reaches the report_only path
        llm, calls = _judge(0.95)
        hit = rc.check(f["reply"], f["user"], f["log"], llm, threshold=0.8, thresholds=ths,
                       confirm=lambda: (0.9, "report_only + agent_fixable"), confirm_floor=rc.CONFIRM_FLOOR)
        fired = bool(hit and hit["rule"] in ("report_only", "status_only"))
        assert fired is f["expect"], f["id"]
        if not f["expect"]:
            assert calls == [], f["id"]  # dropped before the judge: no AI call, no AI score at the edge of a bar
            assert rc.report_only_guard(f["user"], f["reply"]) == "scheduled_wait", f["id"]
        else:
            assert rc.report_only_guard(f["user"], f["reply"]) is None, f["id"]


def test_finding_2_status_question_with_scheduled_retention_card_does_not_fire_any_rule():
    f = _fx("status-scheduled-retention-20261025")
    llm, calls = _judge(1.0)
    assert rc.check(f["reply"], f["user"], f["log"], llm, threshold=0.8, only=["report_only", "status_only"]) is None
    assert calls == []
    assert "retention" in rc.scheduled_wait_status(f["user"], f["reply"]) or "scheduled" in rc.scheduled_wait_status(
        f["user"], f["reply"])


def test_scheduled_wait_needs_a_status_question_and_a_named_wait():
    reply = _fx("status-scheduled-retention-20261025")["reply"]
    for ask in ("is this session done? it was green but not anymore", "why is it not green?", "status?",
                "is the export job finished?", "what's left?"):
        assert rc.scheduled_wait_status(ask, reply), ask
    for ask in ("fix the export job", "is it done? finish it", "can you check whether it's done?", "please close it",
                "thanks"):
        assert rc.scheduled_wait_status(ask, reply) is None, ask
    ask = "is this session done?"
    assert rc.scheduled_wait_status(ask, "The panel is still stale; I haven't fixed it.") is None  # no wait named
    assert rc.scheduled_wait_status(ask, "It is scheduled for Monday, but the build is broken.") is None
    assert rc.scheduled_wait_status(ask, "The soak window is open and the app is still timing out.") is None


def test_scheduled_wait_is_exempt_only_when_it_is_the_sole_open_item():
    ask = "is it done?"
    for reply in ("No. The migration is half finished; I still need to run step 3. The cleanup card is scheduled.",
                  "Not quite: I haven't pushed the fix yet, and the deploy card is parked until tomorrow.",
                  "The deploy card is parked. The fix is not yet committed.",
                  "The release card wakes at 9:00; the docs change is still left to do.",
                  "It is scheduled for Monday; the next step is to run the backfill myself.",
                  "The rollout card is parked; I have yet to update the README.",
                  "The retention card is scheduled. TODO: rerun the eval."):
        assert rc.scheduled_wait_status(ask, reply) is None, reply
    for reply in ("Tests pass. The release card is scheduled and wakes at 9:00 by design.",
                  "Everything is pushed and green. The retention card is parked on purpose until Nov 1, so the "
                  "session remains open until that card completes.",
                  _fx("status-scheduled-retention-20261025")["reply"]):
        assert rc.scheduled_wait_status(ask, reply), reply
    # probe 4 of the review: a failure next to the wait is never exempt (guard level; the prefilter has its own recall)
    assert rc.report_only_guard("is this session done?", "2 tests are failing. The retention card is scheduled.") is None


def test_judge_and_second_opinion_prompts_carry_the_exemption():
    assert "scheduled or parked on purpose" in rc.JUDGE_SYS
    assert "scheduled or parked on purpose" in rc.REPORT_ONLY_RULE  # the slice the second opinion reuses
    assert "scheduled or parked on purpose" in rc.CONFIRM_SYS
    assert rc.DEFAULT_THRESHOLDS == {"report_only": 0.5, "report_only_screen": 0.85}  # no retune from 5 samples


# ── 1. judged-draft retention ────────────────────────────────────────────────────────────────────────────────────────
def _hooks(tmp_path, monkeypatch, name, verdict_p=0.93, confirm_label="report_only"):
    monkeypatch.delenv("COACH_REPLY_RULES", raising=False)
    monkeypatch.delenv("COACH_DRAFTS", raising=False)
    spec = importlib.util.spec_from_file_location(name, HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load("core")
    rcl = mod._load("reply_check")
    monkeypatch.setattr(rcl, "aux_judge", lambda s, p: json.dumps(_verdict(verdict_p)))
    monkeypatch.setattr(rcl, "aux_confirm", lambda s, p: json.dumps(
        {"outcome": confirm_label, "agent_fixable": True, "quote": "stops at the diagnosis"}))
    store = core.Store(root=tmp_path / "coach")
    coach, hooks = mod.build(store=store, checkout=None)
    return mod, store, hooks


ASK = "why is the usage panel stale?"
DRAFT = ("The panel reads a cached snapshot that never refreshes after a restart, so it shows yesterday's numbers. "
         "I haven't fixed it; the cache refresh is not fixed yet.")


def test_nudged_draft_is_recoverable_by_event_after_a_later_turn(tmp_path, monkeypatch):
    mod, store, hooks = _hooks(tmp_path, monkeypatch, "coach_init_drafts1")
    hooks["pre_llm_call"](session_id="sd", user_message=ASK)
    hooks["post_tool_call"](session_id="sd", tool_name="terminal", args={"command": "cat /tmp/raw-tool-input.txt"},
                            result='{"output": "RAW TOOL OUTPUT 12345", "exit_code": 0}')
    out = hooks["pre_verify"](session_id="sd", attempt=0, final_response=DRAFT)
    assert out and out["action"] == "continue"
    hooks["post_llm_call"](session_id="sd", assistant_response=DRAFT)
    # a later turn in the same session overwrites the session's last_reply
    later = "Fixed: the cache now refreshes on restart, verified on the live panel."
    hooks["pre_llm_call"](session_id="sd", user_message="ok fix it")
    hooks["pre_verify"](session_id="sd", attempt=0, final_response=later)
    hooks["post_llm_call"](session_id="sd", assistant_response=later)

    logged = [json.loads(l) for l in (store.root / "log.jsonl").read_text().splitlines()]
    nudge = next(r for r in logged if r["event"] == "reply_nudge")
    confirm = next(r for r in logged if r["event"] == "report_only_confirm")
    assert nudge["draft_id"] and nudge["draft_id"] == confirm["draft_id"]  # one judged draft, both events link it
    d = dr.Drafts(store.root).for_event(nudge)
    assert d["draft"] == DRAFT and d["ask"] == ASK and d["session_id"] == "sd"
    raw = (store.root / "drafts.jsonl").read_text()
    assert "RAW TOOL OUTPUT" not in raw and "raw-tool-input" not in raw  # no tool calls or outputs retained
    assert raw.count('"draft_id"') == 1  # the un-nudged later turn stored nothing


def test_no_draft_without_a_judged_event_and_store_can_be_turned_off(tmp_path, monkeypatch):
    mod, store, hooks = _hooks(tmp_path, monkeypatch, "coach_init_drafts2", verdict_p=0.1)
    hooks["pre_llm_call"](session_id="q", user_message=ASK)
    assert hooks["pre_verify"](session_id="q", attempt=0, final_response=DRAFT) is None
    assert not (store.root / "drafts.jsonl").exists()
    mod, store, hooks = _hooks(tmp_path / "off", monkeypatch, "coach_init_drafts3")
    monkeypatch.setenv("COACH_DRAFTS", "off")
    hooks["pre_llm_call"](session_id="q", user_message=ASK)
    assert hooks["pre_verify"](session_id="q", attempt=0, final_response=DRAFT)  # the nudge itself still fires
    logged = [json.loads(l) for l in (store.root / "log.jsonl").read_text().splitlines()]
    assert all("draft_id" not in r for r in logged) and not (store.root / "drafts.jsonl").exists()


def test_scheduled_wait_turn_logs_the_guard_and_no_nudge(tmp_path, monkeypatch):
    mod, store, hooks = _hooks(tmp_path, monkeypatch, "coach_init_drafts4", verdict_p=0.99)
    f = _fx("status-scheduled-retention-20261025")
    hooks["pre_llm_call"](session_id="sw", user_message=f["user"])
    hooks["post_tool_call"](session_id="sw", tool_name="kanban_show", args={"task_id": "t_d3bbdde0"}, result="{}")
    assert hooks["pre_verify"](session_id="sw", attempt=0, final_response=f["reply"]) is None
    logged = [json.loads(l) for l in (store.root / "log.jsonl").read_text().splitlines()]
    assert not any(r["event"] in ("reply_nudge", "report_only_confirm") for r in logged)
    assert any(r["event"] == "reply_checked" and r.get("guard") == "scheduled_wait" for r in logged)


def test_drafts_are_redacted_before_they_are_cut(tmp_path):
    store = dr.Drafts(tmp_path)
    secret_tok = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    draft = f"Token {secret_tok} and password=hunter2hunter2 are in the config. " + "x" * 9000
    store.put("d_1", session_id="s", ask=f"use api_key=sk-{'Z' * 30}", draft=draft)
    raw = (tmp_path / "drafts.jsonl").read_text()
    assert secret_tok not in raw and "hunter2hunter2" not in raw and "Z" * 30 not in raw
    rec = store.get("d_1")
    assert len(rec["draft"]) < dr.DRAFT_CHARS + 60 and rec["draft"].endswith("more chars]")
    assert dr.redact("plain words stay") == "plain words stay"


def test_drafts_size_cap_drops_oldest_first(tmp_path):
    store = dr.Drafts(tmp_path, max_bytes=6000)
    for i in range(60):
        store.put(f"d_{i}", session_id="s", ask="a", draft=f"draft {i} " + "y" * 200)
    assert (tmp_path / "drafts.jsonl").stat().st_size <= 6000
    assert store.get("d_59")["draft"].startswith("draft 59 ")
    assert store.get("d_0") is None


def test_drafts_age_cap_expires_and_compacts(tmp_path):
    now = [1_000_000.0]
    store = dr.Drafts(tmp_path, clock=lambda: now[0], max_age_s=3600)
    store.put("d_old", session_id="s", ask="a", draft="old draft")
    assert store.get("d_old")["draft"] == "old draft"
    now[0] += 7200
    assert store.get("d_old") is None  # expired on read
    store.put("d_new", session_id="s", ask="a", draft="new draft")
    raw = (tmp_path / "drafts.jsonl").read_text()
    assert "d_old" not in raw and "d_new" in raw  # aged head record compacted away on the next write
    assert store.for_event({"event": "reply_nudge", "draft_id": "d_new"})["draft"] == "new draft"
    assert store.for_event({"event": "reply_nudge"}) is None
