import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t_lessons", HERE / "lessons.py")
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)

V = {"p_correction": 0.9, "durable": 0.9, "rule": "Fetch credentials from token-broker before asking the user.",
     "slug": "Fetch creds before asking!", "scope": "global"}


def test_prefilter():
    assert ls.prefilter("no, get the token from secrets manager, it's there")
    assert ls.prefilter("dont ask me, you should know what dot is running")
    assert not ls.prefilter("great, ship it")


def test_assess_thresholds_and_parse():
    ok = ls.assess("Paste your Asana token.", "no, grab it from the token broker", lambda s, p: json.dumps(V))
    assert ok["slug"] == "fetch-creds-before-asking" and ok["rule"].startswith("Fetch")
    one_off = dict(V, durable=0.2)
    assert ls.assess("x", "no, use 200 not 300", lambda s, p: json.dumps(one_off)) is None
    assert ls.assess("", "no, wrong", lambda s, p: json.dumps(V)) is None  # no previous agent message
    assert ls.assess("x", "looks great", lambda s, p: json.dumps(V)) is None  # prefilter miss


def test_store_promotes_on_second_session_and_records_decisions(tmp_path):
    st = ls.LessonStore(tmp_path)
    v = ls.parse(json.dumps(V))
    assert st.add(v, "s1", "no, token broker")["status"] == "candidate"
    assert st.add(v, "s1", "again")["status"] == "candidate"  # same session doesn't count twice
    assert st.add(v, "s2", "dont ask me")["status"] == "promoted"
    assert [c["slug"] for c in st.by_status("promoted")] == ["fetch-creds-before-asking"]
    st.set_status("fetch-creds-before-asking", "rejected")
    assert st.add(v, "s3")["status"] == "rejected"  # a rejected rule stays rejected


def test_hint_text():
    v = ls.parse(json.dumps(V))
    assert "memory tool" not in ls.hint(v, promoted=False)
    assert "memory tool" in ls.hint(v, promoted=True)


def test_hook_records_lesson(tmp_path, monkeypatch):
    ispec = importlib.util.spec_from_file_location("coach_init_ls", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(ispec)
    ispec.loader.exec_module(mod)
    core, lsm, flm = mod._load("core"), mod._load("lessons"), mod._load("fix_loop")
    monkeypatch.setattr(lsm, "aux_judge", lambda s, p: json.dumps(V))
    monkeypatch.setattr(flm, "aux_judge", lambda s, p: json.dumps({"p_failed_fix": 0.0}))
    store = core.Store(root=tmp_path / "coach")
    _, hooks = mod.build(store=store, checkout=None)
    hooks["pre_llm_call"](session_id="s1", user_message="set up asana")
    hooks["post_llm_call"](session_id="s1", assistant_response="Please paste your Asana token.")
    out = hooks["pre_llm_call"](session_id="s1", user_message="no, get it from the token broker")
    assert out and "Implied standing rule" in out["context"]
    assert (tmp_path / "coach" / "lessons.json").exists()
