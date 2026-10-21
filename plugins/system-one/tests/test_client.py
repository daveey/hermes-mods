import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("s1_client_test", ROOT / "client.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

Q = {"q": c.noul("Is it raining?", true="yes", false="no")}


def setup(monkeypatch, tmp_path, key="k"):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TYPESAFE_API_KEY", key)
    for v in ("SYSTEM_ONE_MODE", "SYSTEM_ONE_MODE_TOPIC_SHIFT", "SYSTEM_ONE_MODEL"):
        monkeypatch.delenv(v, raising=False)


def logs(tmp_path):
    p = tmp_path / "system-one" / "log.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_decide_success_logs_numbers_not_state(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    seen = {}

    def transport(body, key, timeout):
        seen.update(json.loads(body), key=key, timeout=timeout)
        return {"model": "jev-1.14.0", "answers": {"q": {"type": "noul", "noul": 0.81}}, "usage": {"input_tokens": 9}}

    ans = c.decide("demo", {"secret_text": "do not log me"}, Q, transport=transport)
    assert c.p(ans, "q") == 0.81
    assert seen["model"] == "jev-1.14.0" and seen["key"] == "k" and seen["questions"] == Q
    raw = (tmp_path / "system-one" / "log.jsonl").read_text()
    assert "do not log me" not in raw and '"k"' not in raw
    assert logs(tmp_path)[-1]["answers"] == {"q": 0.81} and logs(tmp_path)[-1]["ok"] is True


def test_failures_return_none(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)

    def boom(*a):
        raise TimeoutError()

    assert c.decide("demo", {}, Q, transport=boom) is None
    assert c.decide("demo", {}, Q, transport=lambda *a: {"answers": {}}) is None   # missing answer
    assert c.decide("demo", {}, Q, transport=lambda *a: {"error": {"code": 400}}) is None
    assert [r["why"] for r in logs(tmp_path)] == ["TimeoutError", "ValueError", "ValueError"]


def test_no_key_never_calls(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, key="")
    called = []
    assert c.decide("demo", {}, Q, transport=lambda *a: called.append(1)) is None
    assert not called and logs(tmp_path)[-1]["why"] == "no_key"


def test_key_from_hermes_env_file(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, key="")
    (tmp_path / ".env").write_text("OTHER=1\nTYPESAFE_API_KEY='fromfile'\n")
    assert c.api_key() == "fromfile"


def test_mode_resolution(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    assert c.mode("topic_shift", default="shadow") == "shadow"
    monkeypatch.setenv("SYSTEM_ONE_MODE", "off")
    assert c.mode("topic_shift", default="shadow") == "off"
    monkeypatch.setenv("SYSTEM_ONE_MODE_TOPIC_SHIFT", "on")
    assert c.mode("topic_shift", default="shadow") == "on"
    monkeypatch.setenv("SYSTEM_ONE_MODE_TOPIC_SHIFT", "bogus")
    assert c.mode("topic_shift", default="shadow") == "shadow"


def test_question_builders():
    assert c.score("how bad?", ["fine", "bad"]) == {"type": "score", "instructions": "how bad?", "criteria": ["fine", "bad"]}
    assert c.choice("which?", {"a": "A"})["criteria"] == {"a": "A"}
    assert "criteria" not in c.noul("plain?")


def test_profile_falls_back_to_root_env(monkeypatch, tmp_path):
    prof = tmp_path / "profiles" / "worker"
    prof.mkdir(parents=True)
    setup(monkeypatch, prof, key="")
    (tmp_path / ".env").write_text("TYPESAFE_API_KEY=rootkey\n")
    assert c.api_key() == "rootkey"
    (prof / ".env").write_text("TYPESAFE_API_KEY=profkey\n")
    assert c.api_key() == "profkey"
