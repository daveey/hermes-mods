import importlib.util
import json
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load("bp_core", ROOT / "core.py")
api = _load("bp_api", ROOT / "dashboard" / "plugin_api.py")

REASON = (
    "Code is on main, hub deployed, ios-app-316 released and verified on the phone; the watch still "
    "reported build 295. After 07:00 PT, resume with the watch updated to 316+ and AirPods connected."
)


def fake_llm(calls, reply='{"steps": ["After 07:00 PT, update the watch app to build 316+.", '
                         '"Connect AirPods to the watch.", "Click Done → unblock."]}'):
    def llm(messages, max_tokens):
        calls.append(messages)
        return reply
    return llm


def test_steps_rewrites_and_caches(tmp_path):
    calls = []
    a = core.steps_for("Music on watch", REASON, llm=fake_llm(calls), home=tmp_path)
    assert a == {"steps": ["After 07:00 PT, update the watch app to build 316+.", "Connect AirPods to the watch.",
                           "Click Done → unblock."], "cached": False}
    b = core.steps_for("Music on watch", REASON, llm=fake_llm(calls), home=tmp_path)
    assert b["cached"] is True and b["steps"] == a["steps"]
    assert len(calls) == 1
    # the prompt carries title + note; system prompt demands a hand-back last step
    user = calls[0][1]["content"]
    assert "Music on watch" in user and "build 295" in user
    assert "LAST step" in calls[0][0]["content"]


def test_changed_reason_or_comments_miss_cache(tmp_path):
    calls = []
    core.steps_for("t", REASON, llm=fake_llm(calls), home=tmp_path)
    core.steps_for("t", REASON + " More.", llm=fake_llm(calls), home=tmp_path)
    core.steps_for("t", REASON, comments=["see build 316 notes"], llm=fake_llm(calls), home=tmp_path)
    assert len(calls) == 3
    assert "COMMENT 1:\nsee build 316 notes" in calls[2][1]["content"]


def test_cached_only_never_calls_model(tmp_path):
    calls = []
    assert core.steps_for("t", REASON, llm=fake_llm(calls), home=tmp_path, cached_only=True) == {"steps": [], "pending": True}
    assert calls == []


def test_llm_failure_fails_open_and_is_not_cached(tmp_path):
    def boom(m, n):
        raise TimeoutError("slow")
    r = core.steps_for("t", REASON, llm=boom, home=tmp_path)
    assert r["steps"] == [] and "TimeoutError" in r["error"]
    calls = []
    assert core.steps_for("t", REASON, llm=fake_llm(calls), home=tmp_path)["cached"] is False


def test_empty_input_skips_model(tmp_path):
    calls = []
    assert core.steps_for("t", "  ", llm=fake_llm(calls), home=tmp_path)["error"] == "empty"
    assert calls == []


def test_parse_steps_tolerates_fences_numbering_and_lists():
    assert core.parse_steps('```json\n{"steps": ["1) Open the app", "2. Tap Update"]}\n```') == ["Open the app", "Tap Update"]
    assert core.parse_steps("1. Plug in the phone\n2) Click Done → unblock") == ["Plug in the phone", "Click Done → unblock"]
    assert core.parse_steps('{"steps": []}') == []
    assert len(core.parse_steps(json.dumps({"steps": [f"s{i}" for i in range(12)]}))) == core.MAX_STEPS


def test_concurrent_callers_share_one_model_call(tmp_path):
    calls = []
    gate = threading.Event()

    def slow(messages, n):
        calls.append(1)
        gate.wait(5)
        return '{"steps": ["A", "B"]}'

    out = []
    ts = [threading.Thread(target=lambda: out.append(core.steps_for("t", REASON, llm=slow, home=tmp_path))) for _ in range(4)]
    for t in ts:
        t.start()
    gate.set()
    for t in ts:
        t.join(10)
    assert len(calls) == 1 and all(o["steps"] == ["A", "B"] for o in out)


def test_rest_route(tmp_path):
    calls = []
    app = FastAPI()
    app.include_router(api.create_router(llm=fake_llm(calls), home=tmp_path), prefix="/api/plugins/blocked-panel")
    c = TestClient(app)
    assert c.get("/api/plugins/blocked-panel/ping").json() == {"ok": True, "api": 1}
    r = c.post("/api/plugins/blocked-panel/steps", json={"title": "x", "reason": REASON, "comments": ["c1"]}).json()
    assert r["steps"][-1] == "Click Done → unblock." and len(calls) == 1
