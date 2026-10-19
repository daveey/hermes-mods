"""System One (Jev) detection path in topic-shift classify. No network."""
from test_topic_shift import FakeLLM, NEW, PRIOR, core, make_db

LLM_SHIFT = '{"p_new": 0.9, "label": "Tax return download", "reason": "unrelated"}'
LLM_SAME = '{"p_new": 0.1, "label": "Wake word", "reason": "follow-up"}'


def test_on_low_jev_skips_llm(tmp_path):
    db = make_db(tmp_path, PRIOR + [NEW])
    llm, seen = FakeLLM(LLM_SHIFT), []
    out = core.classify("s1", NEW, llm, db, jev=lambda st: seen.append(st) or 0.1, mode="on")
    assert out["shift"] is False and out["judge"] == "jev" and llm.calls == []
    assert seen[0]["newest_message"] == NEW and seen[0]["recent_user_messages"] == PRIOR


def test_on_high_jev_decides_llm_only_labels(tmp_path):
    db = make_db(tmp_path, PRIOR + [NEW])
    llm = FakeLLM(LLM_SAME)  # the LLM disagrees, but in "on" mode Jev decides
    out = core.classify("s1", NEW, llm, db, jev=lambda st: 0.85, mode="on")
    assert out["shift"] is True and out["confidence"] == 0.85 and out["label"] == "Wake word" and len(llm.calls) == 1


def test_shadow_llm_decides(tmp_path):
    db = make_db(tmp_path, PRIOR + [NEW])
    llm = FakeLLM(LLM_SHIFT)
    out = core.classify("s1", NEW, llm, db, jev=lambda st: 0.05, mode="shadow")
    assert out["shift"] is True and out["confidence"] == 0.9 and "judge" not in out and len(llm.calls) == 1


def test_jev_unavailable_falls_back(tmp_path):
    db = make_db(tmp_path, PRIOR + [NEW])
    for jev in (lambda st: None, lambda st: 1 / 0):
        llm = FakeLLM(LLM_SHIFT)
        out = core.classify("s1", NEW, llm, db, jev=jev, mode="on")
        assert out["shift"] is True and out["confidence"] == 0.9 and len(llm.calls) == 1


def test_skip_rules_run_before_jev(tmp_path):
    db = make_db(tmp_path, PRIOR + ["ok"])
    called = []
    out = core.classify("s1", "ok", FakeLLM(LLM_SHIFT), db, jev=lambda st: called.append(1), mode="on")
    assert out["skipped"] == "short" and not called


def test_default_mode_is_shadow_and_env_flips(monkeypatch):
    monkeypatch.delenv("SYSTEM_ONE_MODE", raising=False)
    assert core.jev_mode() == "shadow"
    monkeypatch.setenv("SYSTEM_ONE_MODE_TOPIC_SHIFT", "on")
    assert core.jev_mode() == "on"
