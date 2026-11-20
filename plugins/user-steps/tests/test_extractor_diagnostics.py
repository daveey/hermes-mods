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
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_USER_STEPS_OFF", raising=False)
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
    span = "Run `sudo mdutil -i off /Volumes/Assets` on the box so indexing stops."
    reply = "The build is staged.\n\n" + span
    step = {"text": "Turn indexing off", "kind": "do", "verbatim_span": span}
    out = run(home, llm_of({"steps": [step]}), reply=reply)
    assert out["steps"] == []
    assert out["dropped"] == ["Turn indexing off"]
    assert reasons(out) == ["terminal_handoff"]


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
    assert "model" not in res["diag"]  # nothing was learned from a model that never answered


def test_fallback_after_model_answer_preserves_model_diagnostic(monkeypatch):
    real = core.validate

    def flaky(raw, *a, **kw):
        if not getattr(flaky, "failed", False):
            flaky.failed = True
            kw["reasons"].append({"reason": "partial", "text": "x"})
            raise RuntimeError("validator broke")
        return real(raw, *a, **kw)
    monkeypatch.setattr(core, "validate", flaky)
    res = core.extract(REPLY, [], "", llm_of({"steps": [STEP, STEP]}))
    assert res["source"] == "regex" and "RuntimeError" in res["error"]
    assert res["diag"]["model"] == {"raw_steps": 2, "raw_steps_type": "list",
                                    "reasons": [{"reason": "partial", "text": "x"}]}
    assert res["diag"]["raw_steps"] == len(core.regex_steps(REPLY))


def test_fallback_model_diagnostic_reaches_process_reply(home, monkeypatch):
    real = core.validate

    def flaky(raw, *a, **kw):
        if not getattr(flaky, "failed", False):
            flaky.failed = True
            raise RuntimeError("validator broke")
        return real(raw, *a, **kw)
    monkeypatch.setattr(core, "validate", flaky)
    out = run(home, llm_of({"steps": [STEP]}))
    assert out["source"] == "regex" and out["diagnostics"]["model"] == {
        "raw_steps": 1, "raw_steps_type": "list", "reasons": []}


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


def test_held_draft_reports_every_candidate(home):
    lg = core.Ledger(home / "user-steps", home / "state.db")
    steps = [{"text": f"Approve the {n} change", "kind": "approve",
              "verbatim_span": f"Should I apply the {n} change now?"} for n in ("first", "second", "third")]
    with lg.edit("S1") as d:
        d["steps"].append({**steps[0], "id": "us_old0000001", "options": [], "status": "resolved", "created": 1,
                           "session": "S1", "paragraph": steps[0]["verbatim_span"]})
    reply = "Checks passed.\n\n" + "\n\n".join(x["verbatim_span"] for x in steps)
    out = run(home, llm_of({"steps": steps}), reply=reply)
    assert out["text"] == core.HELD_REPLY and out["steps"] == []
    d = out["diagnostics"]
    assert d["validated"] == 3 and d["published"] == 0
    assert sorted((p["reason"], p["text"]) for p in d["publication_drops"]) == sorted([
        ("retired_action_hold", steps[0]["text"]),
        ("held_with_retired_action", steps[1]["text"]),
        ("held_with_retired_action", steps[2]["text"])])


def test_system_prompt_directs_more_than_four_options_to_input():
    assert "more than 4 options" in core.SYSTEM and 'kind "input"' in core.SYSTEM


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


MIX_SPAN = "Alpha plan, or Beta plan?"
MIX_REPLY = ("We can ship any of these.\n\n"
             "Alpha plan, or Beta plan? Also consider Gamma plan, Delta plan, and Epsilon plan.")
MIX_OPTS = ["Gamma plan", "Delta plan", "Epsilon plan"]
MIX_LABELS = ["Alpha plan", "Beta plan", "Gamma plan", "Delta plan", "Epsilon plan"]


def test_mixed_derived_and_literal_options_over_four_become_input(home):
    """2 derived alternatives plus 3 literal model options used to be sliced to 4 buttons."""
    step = {"text": "Pick a plan", "kind": "choose", "verbatim_span": MIX_SPAN,
            "options": MIX_OPTS, "paragraph": MIX_REPLY}
    assert core.literal_options(step) == []
    out = run(home, llm_of({"steps": [{"text": "Pick a plan", "kind": "choose",
                                       "verbatim_span": MIX_SPAN, "options": MIX_OPTS}]}),
              reply=MIX_REPLY)
    assert len(out["steps"]) == 1
    published = out["steps"][0]
    assert published["kind"] == "input" and published["options"] == []
    text = published.get("paragraph") or MIX_REPLY
    for label in MIX_LABELS:
        assert label in text


def test_transform_llm_output_trace_carries_diagnostics(home, monkeypatch):
    monkeypatch.setitem(sys.modules, "user_steps_core", core)
    spec = importlib.util.spec_from_file_location("diag_plugin", ROOT / "__init__.py")
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    monkeypatch.setattr(plugin.core, "home", lambda: home)
    monkeypatch.setattr(plugin, "aux_llm", llm_of({"steps": [STEP], "resolved": []}))
    plugin.transform_llm_output(REPLY, "S1", "desktop")
    rows = [json.loads(line) for line in (home / "cache" / "user-steps" / "trace.jsonl").read_text().splitlines()
            if line.strip()]
    trans = [row for row in rows if row.get("hook") == "transform" and "diagnostics" in row]
    assert trans, rows
    d = trans[-1]["diagnostics"]
    assert d["raw_steps"] == 1 and d["validated"] == 1 and d["published"] == 1
    assert d["validation_drops"] == [] and d["publication_drops"] == []


OWNER = {"gatewayId": "g", "profile": "default", "storedSessionId": "S1"}
# Shapes from the hub ledger (wording changed, structure kept): the authored labels are short
# paraphrases of an 'A, or B?' question, so they are not substrings of its alternatives.
AUTHORED = [
    ("Submit the promotion PR to the research repo as-is (negative result), or run follow-ups 1-4 first "
     "(about 4 h on one GPU) and fold them in?",
     ["Submit as-is", "Run the follow-ups first"]),
    ("Build the ring ingestion plan as written (dedicated client node, mail-tick pull with a wake file, "
     "Inbox note for stale or empty captures, audio kept for 30 days)?",
     ["Go: build as written", "Change a decision first"]),
    ("Sweep the 18 red cards with no live owner: re-check each premise, archive or finish the stale and "
     "agent-doable ones, link the rest to this chat, and bring you only the real decisions?",
     ["Yes, sweep them", "Just link them here, no triage", "Leave them"]),
    ("The gate card has used 73 runs against a 15-run budget and shipped nothing. Stop the card, or keep "
     "going with a smaller scope?",
     ["Archive it (recommended)", "Continue 12 runs: build and ship the gate",
      "Continue 5 runs: alarm only, drop the gate"]),
]


@pytest.mark.parametrize("question,labels", AUTHORED)
def test_authored_choices_stay_exactly_the_buttons(home, question, labels):
    """An add_step menu is the whole menu: the question's own 'A, or B?' halves are never added
    to it (2 buttons became 4) and never push it over the limit (3 buttons became none)."""
    lg = core.Ledger(home / "user-steps", home / "state.db")
    step = core.add_step("S1", question, labels, ledger=lg)
    assert step["kind"] == "choose"
    assert core.literal_options(step) == labels
    r = core.legacy_request(OWNER, step, {"kind": "step", "id": step["id"]})
    assert r["kind"] == "choice"
    assert [c["label"] for c in r["choices"]][:-1] == labels


@pytest.mark.parametrize("span,opts,paragraph", [
    # options restate the question's alternatives
    ("Do you want the short version, or the long version?", ["short version", "long version"],
     "Do you want the short version, or the long version?"),
    # an unrelated closing question after the real menu
    ("Should I start it now, or wait for you?", ["Fast path", "Slow path", "Hybrid path"],
     "Options: Fast path, Slow path, Hybrid path. Should I start it now, or wait for you?"),
    ("Should I pick one for you, or wait here?", ["One a", "Two b", "Three c", "Four d"],
     "One a, Two b, Three c, Four d. Should I pick one for you, or wait here?"),
    # paraphrases of both alternatives plus a third option: still the model's menu
    ("Ship the cache patch today, or hold the cache patch for review?",
     ["ship today", "hold for review", "drop the patch"],
     "Ship the cache patch today, or hold the cache patch for review? "
     "In short: ship today, hold for review, or drop the patch."),
])
def test_extracted_options_that_answer_the_question_keep_their_labels(span, opts, paragraph):
    step = {"text": "Pick", "kind": "choose", "verbatim_span": span, "options": opts, "paragraph": paragraph}
    assert core.literal_options(step) == opts
    r = core.legacy_request(OWNER, step, {"kind": "step", "id": "x"})
    assert r["kind"] == "choice" and [c["label"] for c in r["choices"]][:-1] == opts
