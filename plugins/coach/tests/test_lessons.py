import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t_lessons", HERE / "lessons.py")
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)

V = {"p_correction": 0.9, "durable": 0.9, "rule": "Fetch credentials from token-broker before asking the user.",
     "slug": "Fetch creds before asking!", "scope": "global"}


def test_prefilter_judges_everything_but_acks():
    assert ls.prefilter("no, get the token from secrets manager, it's there")
    assert ls.prefilter("asana pat is in the secrets manager")  # no correction keyword, still judged
    assert ls.prefilter("left frame didnt change")
    for ack in ("go", "do it", "yes!", "ok", "/compress", "[image]", "ship it"):
        assert not ls.prefilter(ack), ack


def test_assess_thresholds_and_parse():
    ok = ls.assess("Paste your Asana token.", "no, grab it from the token broker", lambda s, p: json.dumps(V), wide=True)
    assert ok["slug"] == "fetch-creds-before-asking" and ok["rule"].startswith("Fetch")
    one_off = dict(V, durable=0.2)
    assert ls.assess("x", "no, use 200 not 300", lambda s, p: json.dumps(one_off)) is None
    assert ls.assess("", "no, wrong", lambda s, p: json.dumps(V)) is None  # no previous agent message
    assert ls.assess("x", "great", lambda s, p: json.dumps(V), wide=True) is None  # bare ack: not judged
    assert ls.assess("x", "asana pat is in the secrets manager", lambda s, p: json.dumps(V)) is None  # narrow gate
    assert ls.assess("x", "asana pat is in the secrets manager", lambda s, p: json.dumps(V), wide=True)


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
    monkeypatch.setenv("COACH_LESSONS", "1")  # opt in with the fake judge below
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
    hooks["pre_llm_call"](session_id="s1", user_message="no, get it from the token broker")
    import threading, time
    for _ in range(50):  # the judge runs in a background thread
        if (tmp_path / "coach" / "lessons.json").exists() and not [t for t in threading.enumerate() if t.daemon and t.is_alive() and t.name.startswith("Thread")]:
            break
        time.sleep(0.05)
    assert (tmp_path / "coach" / "lessons.json").exists()
    res = hooks["transform_tool_result"](tool_name="terminal", result='{"output": "x"}', session_id="s1")
    assert "Implied standing rule" in json.loads(res)["coach_hint"]


def test_known_rules_go_into_prompt_and_saved_stays_saved(tmp_path):
    st = ls.LessonStore(tmp_path)
    v = ls.parse(json.dumps(V))
    st.add(v, "s1"); st.set_status(v["slug"], "saved")
    assert [c["slug"] for c in st.known()] == [v["slug"]]
    seen = []
    ls.assess("x", "asana pat is in secrets", lambda s, p: seen.append(p) or json.dumps(V), wide=True, known=st.known())
    assert v["slug"] in seen[0]
    assert st.add(v, "s2")["status"] == "saved"
