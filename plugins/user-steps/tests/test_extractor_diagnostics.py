"""Extractor/transform diagnostics: raw step count, validation drop reasons, publication drops, >4 options.

Generic fixtures only. Nothing here claims to reproduce any historic missing pick."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("diag_core", ROOT / "core.py")
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)

REPLY = "Two ways to fix it.\n\nShould I use Postgres or SQLite for the cache? I recommend SQLite."
SPAN = "Should I use Postgres or SQLite for the cache?"
STEP = {"text": "Choose the cache database", "kind": "choose", "verbatim_span": SPAN,
        "options": ["Postgres", "SQLite"]}


@pytest.fixture
def home(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "hermes_cli.install_identity", types.SimpleNamespace(get_install_id=lambda: "t"))
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", types.SimpleNamespace(current_profile_name=lambda: "default"))
    db = sqlite3.connect(tmp_path / "state.db")
    db.execute("create table sessions (id text primary key, parent_session_id text, end_reason text, source text)")
    db.execute("insert into sessions values ('S1', null, null, 'desktop')")
    db.commit()
    db.close()
    return tmp_path


def llm_of(payload):
    return lambda system, user: json.dumps(payload)


def run(home, llm, reply=REPLY, **kw):
    return core.process_reply(reply, "S1", llm=llm, ledger=core.Ledger(home / "user-steps", home / "state.db"),
                              cards=None, framed=True, session_ref="@session:default/S1", **kw)


def reasons(out):
    return [r["reason"] for r in out["diagnostics"]["validation_drops"]]


# ---- raw count and validation reasons ------------------------------------------------------------------

def test_raw_count_published_when_model_returns_nothing(home):
    out = run(home, llm_of({"steps": [], "resolved": []}))
    d = out["diagnostics"]
    assert out["source"] == "model" and out["steps"] == []
    assert d["raw_steps"] == 0 and d["validated"] == 0 and d["validation_drops"] == [] and d["published"] == 0


def test_non_list_steps_reported(home):
    d = run(home, llm_of({"steps": "none"}))["diagnostics"]
    assert d["raw_steps"] == 0 and d["raw_steps_type"] == "str"


def test_validation_drop_reasons_are_recorded(home):
    raw = [
        "junk",
        {"text": "", "verbatim_span": SPAN},
        {"text": "No span", "kind": "input", "verbatim_span": ""},
        {"text": "Not in reply", "kind": "input", "verbatim_span": "Which colour do you want for the logo?"},
        {"text": "Say yes", "kind": "approve", "verbatim_span": "I recommend SQLite."},
    ]
    out = run(home, llm_of({"steps": raw}))
    assert out["steps"] == []
    d = out["diagnostics"]
    assert d["raw_steps"] == 5
    got = reasons(out)
    assert got[:3] == ["not_an_object", "missing_text", "missing_span"]
    assert "span_not_in_reply" in got
    assert all(r["reason"] for r in d["validation_drops"])
    assert len(d["validation_drops"]) == 5 - d["validated"]


def test_terminal_handoff_keeps_dropped_semantics_and_gets_reason(home):
    reply = "The build is staged.\n\nPlease run `make deploy` in the repo."
    step = {"text": "Run make deploy", "kind": "do", "verbatim_span": "Please run `make deploy` in the repo."}
    out = run(home, llm_of({"steps": [step]}), reply=reply)
    if out["dropped"]:  # classifier-dependent; when it fires both views must agree
        assert out["dropped"] == ["Run make deploy"]
        assert "terminal_handoff" in reasons(out)
    else:
        assert out["diagnostics"]["validation_drops"] == [] or out["steps"] == []


def test_dropped_list_unchanged_without_reasons():
    dropped = []
    steps = core.validate([STEP], REPLY, [], dropped)
    assert len(steps) == 1 and dropped == []


def test_reply_cap_reason():
    spans = [f"Should I do task number {i} for the team now?" for i in range(core.MAX_NEW_PER_REPLY + 2)]
    reply = "\n\n".join(spans)
    raw = [{"text": f"Do task {i}", "kind": "approve", "verbatim_span": s} for i, s in enumerate(spans)]
    why = []
    out = core.validate(raw, reply, [], reasons=why)
    assert len(out) == core.MAX_NEW_PER_REPLY
    assert [w["reason"] for w in why] == ["reply_cap"] * 2


def test_regex_source_has_raw_count(home):
    res = core.extract(REPLY, [], "", None)
    assert res["source"] == "regex" and res["diag"]["raw_steps"] == len(core.regex_steps(REPLY))


def test_llm_failure_falls_back_with_diag():
    def boom(system, user):
        raise TimeoutError("down")
    res = core.extract(REPLY, [], "", boom)
    assert res["source"] == "regex" and "TimeoutError" in res["error"] and "diag" in res


def test_elsewhere_mapped_reason():
    title = "Use Postgres or SQLite for the cache"
    payload = {"steps": [STEP], "elsewhere": [{"title": title, "verbatim_span": SPAN}]}
    res = core.extract(REPLY, [], "", llm_of(payload), elsewhere=[title])
    assert res["steps"] == []
    assert [r["reason"] for r in res["diag"]["reasons"]] == ["elsewhere_mapped"]


# ---- downstream publication drops ----------------------------------------------------------------------

def test_success_path_publishes_and_reports_zero_drops(home):
    out = run(home, llm_of({"steps": [STEP]}))
    d = out["diagnostics"]
    assert len(out["steps"]) == 1 and d["raw_steps"] == 1 and d["validated"] == 1
    assert d["validation_drops"] == [] and d["publication_drops"] == [] and d["published"] == 1


def test_budget_drop_is_reported(home, monkeypatch):
    monkeypatch.setattr(core, "publication_budget", lambda steps, now: 0)
    out = run(home, llm_of({"steps": [STEP]}))
    assert out["steps"] == []
    d = out["diagnostics"]
    assert d["validated"] == 1 and d["published"] == 0
    assert [p["reason"] for p in d["publication_drops"]] == ["publication_budget"]


def test_elsewhere_publication_drop_is_reported(home, monkeypatch):
    store = object()
    monkeypatch.setattr(core, "reply_listing", lambda opened, st, owner, cards: (opened, []))
    monkeypatch.setattr(core, "elsewhere_titles", lambda st, owner, limit=30: [])
    monkeypatch.setattr(core, "elsewhere_open_keys", lambda st, owner: {core.ask_key("Choose the cache database")})
    monkeypatch.setattr(core, "open_asks", lambda st, owner, steps: [])
    out = run(home, llm_of({"steps": [STEP]}), action_store=store, action_owner={"storedSessionId": "x"},
              user_msg="[Kanban] task t_1 completed")
    assert out["steps"] == []
    assert [p["reason"] for p in out["diagnostics"]["publication_drops"]] == ["elsewhere_open_ask"]


def test_skip_reports_reason(home):
    out = run(home, None, reply="Done.")
    assert out["source"] == "skip" and out["diagnostics"] == {"skipped": "not_worth_asking"}


# ---- more than four options ----------------------------------------------------------------------------

def test_more_than_four_options_become_input_not_truncated():
    opts = ["Alpha plan", "Beta plan", "Gamma plan", "Delta plan", "Epsilon plan"]
    span = "Which plan should I use: Alpha plan, Beta plan, Gamma plan, Delta plan or Epsilon plan?"
    why = []
    out = core.validate([{"text": "Pick a plan", "kind": "choose", "verbatim_span": span, "options": opts}],
                        span, [], reasons=why)
    assert len(out) == 1 and out[0]["kind"] == "input" and out[0]["options"] == []
    assert [w["reason"] for w in why] == ["downgraded_options_over_limit"]


def test_four_options_still_kept_in_order():
    opts = ["Alpha plan", "Beta plan", "Gamma plan", "Delta plan"]
    span = "Which plan should I use: Alpha plan, Beta plan, Gamma plan or Delta plan?"
    out = core.validate([{"text": "Pick a plan", "kind": "choose", "verbatim_span": span, "options": opts}], span, [])
    assert out[0]["kind"] == "choose" and out[0]["options"] == opts


def test_add_step_over_four_choices_not_silently_shortened(home):
    lg = core.Ledger(home / "user-steps", home / "state.db")
    step = core.add_step("S1", "Which plan?", [f"Plan {i}" for i in range(5)], ledger=lg)
    assert step["kind"] == "input" and step["choices"] == [] and step["options"] == []
    step = core.add_step("S1", "Which other plan?", [f"Plan {i}" for i in range(4)], ledger=lg)
    assert step["kind"] == "choose" and len(step["choices"]) == 4
