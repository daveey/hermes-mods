"""self_heal: complaint (pre_llm_call) and self-admission (post_llm_call) detectors, fingerprint dedupe, modes."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sh = load("coach_self_heal_test", ROOT / "self_heal.py")
core = load("coach_core_test_sh", ROOT / "core.py")
plugin = load("coach_plugin_test_sh", ROOT / "__init__.py")


def judge(p=0.9, component="desktop", symptom="sidebar sessions reshuffle"):
    calls = []

    def llm(system, prompt):
        calls.append((system, prompt))
        return json.dumps({"p_defect": p, "component": component, "symptom": symptom})
    return llm, calls


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("COACH_SELFHEAL", "shadow")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)


def events(tmp_path, name="selfheal_detect"):
    p = tmp_path / "coach" / "log.jsonl"
    if not p.exists():
        return []
    return [r for r in (json.loads(x) for x in p.read_text().splitlines()) if r.get("event") == name]


# ── prefilters ──
@pytest.mark.parametrize("text", [
    "i think the sort is broken",
    "why are you asking me to open a terminal?",
    "i fixed this once, but now it's broken again",
    "you said that, but i dont see any cards scheduled",
    "fix this [screenshot]",
    "[OUT-OF-BAND USER MESSAGE — x]\nYOU JUST SAID IT WAS CANCELED, WHY DO YOU KEEP ASKING ME\n[/OUT-OF-BAND USER MESSAGE]",
    "it's speaking spanish to me for some reason",
])
def test_complaint_prefilter_hits(text):
    assert sh.prefilter(text)


@pytest.mark.parametrize("text", [
    "do it", "resume", "go", "/goal ship it",
    "[IMPORTANT: Background process proc_1 exited (exit code 0).]",
    "✔ [default] Kanban t_1 done — shipped",
    "make a graph of my steps and sleep",
])
def test_complaint_prefilter_skips(text):
    assert not sh.prefilter(text)


def test_multimodal_message_and_trailers_are_cleaned():
    msg = [{"type": "text", "text": "fix this"}, {"type": "image_url", "image_url": {"url": "data:..."}}]
    assert sh.clean_user(msg) == "fix this [screenshot]" and sh.prefilter(msg)
    assert sh.clean_user("didnt work [screenshot] [user-steps] Steps still waiting on the user") == "didnt work [screenshot]"


def test_admission_prefilter():
    assert sh.admission_prefilter("You're right, and that was my mistake. I skipped the check.")
    assert sh.admission_prefilter("The root cause is a bug in the fallback plugin's import lock.")
    assert not sh.admission_prefilter("Shipped. Tests pass and the page is live.")


# ── detect_complaint / detect_admission ──
def test_detect_complaint_returns_schema_and_passes_prev_reply():
    llm, calls = judge(0.92)
    hit = sh.detect_complaint("Done: sessions are sorted by creation time.", "i think the sort is broken", llm)
    assert set(hit) >= {"p_defect", "component", "symptom", "fingerprint", "source"}
    assert hit["source"] == "complaint" and hit["component"] == "desktop" and hit["p_defect"] == 0.92
    assert "sorted by creation time" in calls[0][1] and "sort is broken" in calls[0][1]


def test_detect_complaint_below_threshold_prefilter_miss_and_judge_error():
    llm, calls = judge(0.4)
    assert sh.detect_complaint("prev", "why did you stop?", llm) is None
    llm2, calls2 = judge(0.99)
    assert sh.detect_complaint("prev", "make a graph of my steps", llm2) is None and not calls2

    def boom(system, prompt):
        raise RuntimeError("provider down")
    assert sh.detect_complaint("prev", "still broken", boom) is None  # fail-open
    assert sh.detect_complaint("prev", "still broken", lambda s, p: "not json") is None


def test_detect_admission():
    llm, calls = judge(0.85, "agent", "asked user for a finished step")
    hit = sh.detect_admission("That was my mistake: I asked you to do something already done.", llm)
    assert hit["source"] == "admission" and hit["component"] == "agent"
    assert calls[0][0] == sh.ADMIT_SYS
    llm2, calls2 = judge(0.99)
    assert sh.detect_admission("All green, deployed.", llm2) is None and not calls2


def test_unknown_component_maps_to_other():
    llm, _ = judge(0.9, component="spaceship")
    assert sh.detect_complaint("", "it crashed again", llm)["component"] == "other"


# ── fingerprint + dedupe ──
def test_fingerprint_stable_across_rewording_and_distinct_otherwise():
    a = sh.fingerprint("desktop", "Sidebar sessions reshuffle")
    b = sh.fingerprint("desktop", "session sidebar reshuffles")
    c = sh.fingerprint("desktop", "draft text lost on new session")
    d = sh.fingerprint("plugin", "sidebar sessions reshuffle")
    assert a == b and a != c and a != d and a.startswith("desktop-")


def test_fingerprints_dedupe_per_utc_day(tmp_path):
    t = [86400 * 100 + 10.0]
    fps = sh.Fingerprints(tmp_path, clock=lambda: t[0])
    hit = {"fingerprint": "desktop-abc", "component": "desktop", "symptom": "x"}
    assert fps.record(hit, "s1") is True
    assert fps.record(hit, "s2") is False  # same day, another session
    t[0] += 86400
    assert fps.record(hit, "s3") is True  # next UTC day files again
    d = json.loads((tmp_path / "selfheal.json").read_text())["desktop-abc"]
    assert d["count"] == 3 and d["sessions"] == ["s1", "s2", "s3"]


# ── through the real hooks (both entry paths) ──
def test_pre_llm_call_complaint_path_logs_shadow_and_dedupes(tmp_path, on):
    llm, calls = judge(0.9)
    store = core.Store(tmp_path / "coach")
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm)
    hist = [{"role": "user", "content": "sort by creation"}, {"role": "assistant", "content": "Sorted by creation time."}]
    hooks["pre_llm_call"](session_id="s1", user_message="i think the sort is broken", conversation_history=hist)
    hooks["pre_llm_call"](session_id="s2", user_message="the sidebar keeps reshuffling, still broken")
    ev = events(tmp_path)
    assert [e["source"] for e in ev] == ["complaint", "complaint"]
    assert ev[0]["mode"] == "shadow" and ev[0]["new"] is True and ev[1]["new"] is False
    assert ev[0]["fingerprint"] == ev[1]["fingerprint"]
    assert "Sorted by creation time." in calls[0][1]  # prev reply from conversation_history
    assert not (tmp_path / "coach" / "selfheal_queue.jsonl").exists()  # shadow never enqueues


def test_post_llm_call_admission_path(tmp_path, on):
    llm, calls = judge(0.88, "agent", "claimed cards scheduled when none were")
    store = core.Store(tmp_path / "coach")
    _, hooks = plugin.build(store=store, checkout=None, selfheal_sync=True, selfheal_llm=llm)
    hooks["post_llm_call"](session_id="s1", assistant_response="You're right, that was my mistake: no cards were scheduled.")
    hooks["post_llm_call"](session_id="s1", assistant_response="Done, deployed and verified.")
    ev = events(tmp_path)
    assert len(ev) == 1 and ev[0]["source"] == "admission" and ev[0]["component"] == "agent"


def test_live_mode_enqueues_once_per_fingerprint(tmp_path, on, monkeypatch):
    monkeypatch.setenv("COACH_SELFHEAL", "live")
    llm, _ = judge(0.9)
    _, hooks = plugin.build(store=core.Store(tmp_path / "coach"), checkout=None, selfheal_sync=True, selfheal_llm=llm)
    hooks["pre_llm_call"](session_id="s1", user_message="still broken")
    hooks["pre_llm_call"](session_id="s1", user_message="still broken!!")
    q = (tmp_path / "coach" / "selfheal_queue.jsonl").read_text().splitlines()
    assert len(q) == 1 and json.loads(q[0])["mode"] == "live"


def test_off_mode_subagents_cron_and_kanban_workers_are_skipped(tmp_path, on, monkeypatch):
    llm, calls = judge(0.9)
    _, hooks = plugin.build(store=core.Store(tmp_path / "coach"), checkout=None, selfheal_sync=True, selfheal_llm=llm)
    hooks["pre_llm_call"](session_id="s1", user_message="still broken", platform="cron")
    hooks["pre_llm_call"](session_id="s1", user_message="still broken", parent_session_id="p1")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    hooks["pre_llm_call"](session_id="s1", user_message="Fix the dispatcher crash")
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    monkeypatch.setenv("COACH_SELFHEAL", "off")
    hooks["pre_llm_call"](session_id="s1", user_message="still broken")
    hooks["post_llm_call"](session_id="s1", assistant_response="that was my mistake")
    assert not calls and events(tmp_path) == []


def test_hook_judge_crash_is_fail_open(tmp_path, on):
    def boom(system, prompt):
        raise RuntimeError("x")
    _, hooks = plugin.build(store=core.Store(tmp_path / "coach"), checkout=None, selfheal_sync=True, selfheal_llm=boom)
    assert hooks["pre_llm_call"](session_id="s1", user_message="still broken") is None
    hooks["post_llm_call"](session_id="s1", assistant_response="my mistake")
    assert events(tmp_path) == []


# ── replay eval (synthetic fixture; the real labeled set is private) ──
def test_replay_reports_catch_rate_precision_misses_and_fps(tmp_path):
    replay = load("selfheal_replay_test", ROOT / "eval" / "selfheal_replay.py")
    defects = {"i think the sort is broken": 0.9, "why are you asking me to open a terminal": 0.95,
               "the menu should be shorter": 0.7, "my alarm is not set": 0.2}

    def llm(system, prompt):
        msg = prompt.split("USER'S NEW MESSAGE:\n", 1)[-1]
        p = next((v for k, v in defects.items() if k in msg), 0.1)
        return json.dumps({"p_defect": p, "component": "desktop", "symptom": "x"})
    out = tmp_path / "out"
    replay.main(["--data", str(ROOT / "tests" / "fixtures" / "selfheal_sample.jsonl"), "--out", str(out),
                 "--workers", "2"], llm=llm)
    rep = json.loads((out / "report.json").read_text())
    a = rep["all"]
    assert (a["pos"], a["tp"], a["fn"], a["fp"]) == (4, 2, 2, 1)
    assert a["catch_rate"] == 0.5 and a["precision"] == round(2 / 3, 3)
    assert {m["id"] for m in rep["misses"]} == {"c", "d"} and [f["id"] for f in rep["false_positives"]] == ["g"]
    assert rep["prefilter_only"]["fn"] == 0  # every defect reaches the judge; "do it" never does
    md = (out / "report.md").read_text()
    assert "my alarm is not set" in md and "False positives (1)" in md
    # cached verdicts: a rerun makes no judge calls
    calls = []
    replay.main(["--data", str(ROOT / "tests" / "fixtures" / "selfheal_sample.jsonl"), "--out", str(out)],
                llm=lambda s, p: calls.append(1) or "{}")
    assert not calls
