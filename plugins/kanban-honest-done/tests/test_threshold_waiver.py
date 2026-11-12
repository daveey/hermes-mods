"""Gate G: a failed acceptance threshold never becomes an automatic waiver or a guessed pass.

Shapes from the three red-sweep `acceptance-waiver-owner-decision` rows (2026-11-12):
- energy filter: two checks failed (culler beats the heuristic; rain 81-82% vs an 80% limit), and the
  sweep's guess "accept with a written waiver" was the next step;
- oracle check 5b: the do-nothing mean 807.35 must be under 800;
- mini disk: 6.1% free against a fixed 25% target, with a proposed 15 GB revision.

Run from the plugin dir:  uv run -q --with pytest python -m pytest -q -p no:cacheprovider tests
"""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plugin = _load("kanban_honest_done_plugin_g", "__init__.py")
core = plugin.core

OWN = "t_0123456789abcdef"

BODY_PLAIN = ("Run the energy filter. Acceptance: every source passes checks 1-5 (check 4: rain is at most 80% "
              "of the heuristic's spend).")
BODY_BRANCHED = BODY_PLAIN + (
    "\nIF FAIL check 4: report FAIL with the measured share, keep the source out of the next phase, and file a decision "
    "card for David with the options.\n"
    "IF FAIL check 1: re-run once on fresh seeds 1020-1039; if it still fails, report FAIL and stop.\n")
BODY_WAIVER_BRANCH = BODY_PLAIN + "\nIF FAIL check 4: accept it for this phase with a written waiver.\n"
BODY_HUMAN_WAIVER = BODY_PLAIN + (
    "\nACCEPTANCE AMENDED (user message 931890): check 4 waived for this phase; the next phase re-measures it.\n")


def board_with(tmp_path, body):
    db = tmp_path / "kanban.db"
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT, status TEXT NOT NULL,
                            created_at INTEGER NOT NULL DEFAULT 0, completed_at INTEGER, session_id TEXT, result TEXT);
        CREATE TABLE task_links (parent_id TEXT NOT NULL, child_id TEXT NOT NULL, PRIMARY KEY (parent_id, child_id));
        CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, run_id INTEGER, kind TEXT,
                                  payload TEXT, created_at INTEGER);
        CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, author TEXT, body TEXT,
                                    created_at INTEGER);
    """)
    c.execute("INSERT INTO tasks (id, title, body, status) VALUES (?,?,?,?)",
              (OWN, "Energy filter", body, "running"))
    c.commit()
    c.close()
    return lambda: [db]


def complete(paths_fn, summary, metadata=None, tool="kanban_complete"):
    args = {"summary": summary}
    if metadata is not None:
        args["metadata"] = metadata
    return plugin.classify(tool, args, OWN, paths_fn)


def result(check, status, **kw):
    return {"acceptance_results": [dict(check=check, status=status, **kw)]}


# --- prose: automatic waiver / guessed pass -----------------------------------------------------------------------

@pytest.mark.parametrize("summary", [
    # energy row: the guessed D1(c)/D2(a) ruling applied as if it were acceptance
    "Checks 1 and 4 failed for every source; applied D1(c): the check 1 failure is accepted for this phase with a written "
    "waiver, and all three sources go to the next phase.",
    "Check 4 failed (rain 81-82% of spend vs the 80% limit); waived as a heuristic artefact.",
    # oracle row: a post-result swap of a fixed gate reported as a pass
    "5b fails at 807.35 against the 800 bar, so I treated it as a pass under the relative drought checks.",
    "The do-nothing mean is 807.35, which is close enough to the 800 bar; scorer v2 landed.",
    # mini disk row: a lowered target reported as met
    "Disk is at 6.1% free, short of the 25% target; 15 GB free counts as a pass for the restart work.",
    "Free space missed the 25% target but effectively passes with 14.99 GB free.",
])
def test_prose_waiver_or_guessed_pass_is_refused(tmp_path, summary):
    why = complete(board_with(tmp_path, BODY_PLAIN), summary)
    assert why and "[kanban-honest-done]" in why and "threshold" in why.lower()


def test_prose_in_metadata_is_refused(tmp_path):
    why = complete(board_with(tmp_path, BODY_PLAIN), "Energy filter finished.",
                   {"decisions": ["check 4 failure waived for this phase"]})
    assert why and "threshold" in why.lower()


def test_prose_through_request_review_is_refused(tmp_path):
    why = complete(board_with(tmp_path, BODY_PLAIN),
                   "Check 4 failed at 82% vs 80%; waived it, ready for review.", tool="kanban_request_review")
    assert why and "threshold" in why.lower()


def test_prose_through_cli_complete_is_refused(tmp_path):
    cmd = (f"hermes kanban complete {OWN} --summary "
           "'Check 4 failed at 82% vs 80%; accepted the failure with a written waiver.'")
    why = plugin.classify("terminal", {"command": cmd}, OWN, board_with(tmp_path, BODY_PLAIN))
    assert why and "threshold" in why.lower()


def test_prose_waiver_allowed_when_body_records_human_waiver(tmp_path):
    # The body waiver could only get there through Gate F (a user message typed in a human chat).
    why = complete(board_with(tmp_path, BODY_HUMAN_WAIVER),
                   "Check 4 failed at 82% vs 80% and is waived for this phase per the amended acceptance; the next phase re-measures it.")
    assert why is None


def test_guessed_pass_never_exempt_even_with_body_waiver(tmp_path):
    why = complete(board_with(tmp_path, BODY_HUMAN_WAIVER),
                   "Check 1 failed by 8%, close enough, so I treated it as a pass.")
    assert why and "threshold" in why.lower()


@pytest.mark.parametrize("summary", [
    "Regression tests failed before the change and pass after it; full suite green.",
    "Check 4 failed (82% vs 80%); reported FAIL and filed the decision for David. Nothing was waived.",
    "Failed thresholds can never become an automatic waiver or a guessed pass; no waiver was applied.",
    "The PR has no required checks, so metadata carries contract_waiver; CI passed.",
    "All five checks pass: observed 0.78 against the 0.80 limit.",
    "Did the do-nothing mean fail and get waived?",
    'The worker quoted "check 4 failed but waived" from the old thread; I did not apply it.',
])
def test_honest_handoffs_pass(tmp_path, summary):
    assert complete(board_with(tmp_path, BODY_PLAIN), summary) is None


def test_contract_waiver_metadata_is_not_a_threshold_waiver(tmp_path):
    md = {"contract_waiver": {"reason": "repo has no required checks; failed check list is empty"}}
    assert complete(board_with(tmp_path, BODY_PLAIN), "Merged.", md) is None


# The energy card's real completed handoff (2026-11-12, project words generalized), applied after a red sweep
# guessed the D1(c) waiver under a blanket delegation: replay shows Gate G refuses it.
ENERGY_HANDOFF = ("All three sources are admitted to the next phase by a phase-only waiver; measured failures, model re-checks, "
                  "culler comparisons and spend gates remain intact.")


def test_real_energy_handoff_is_refused(tmp_path):
    why = complete(board_with(tmp_path, BODY_PLAIN), ENERGY_HANDOFF)
    assert why and "phase-only waiver" in why


@pytest.mark.parametrize("summary", [
    "Check 4 did not pass at 82%, so I waived it.",
    "5b misses the 800 bar at 807.35 but is within noise.",
])
def test_negation_elsewhere_does_not_hide_a_waiver(tmp_path, summary):
    assert complete(board_with(tmp_path, BODY_PLAIN), summary)


@pytest.mark.parametrize("summary", [  # real replay false-positive shapes, kept as controls
    "Did not revalidate implementation test artifacts or waive any failures; the owner must revalidate evidence.",
    "Never extend production lease, mock apply success or waive failures.",
    "Flips main-vs-main 43, main-vs-new 49 (within noise).",
    "bb-size-ok security-cap waiver tests pass; negative control fails on old code.",
])
def test_replay_controls_pass(tmp_path, summary):
    assert complete(board_with(tmp_path, BODY_PLAIN), summary) is None


# --- structured metadata.acceptance_results -----------------------------------------------------------------------

def test_pass_contradicting_threshold_is_refused(tmp_path):
    md = result("5b", "pass", threshold=800, comparator="<", observed=807.35)
    why = complete(board_with(tmp_path, BODY_PLAIN), "Oracle checks done.", md)
    assert why and "5b" in why and "guessed pass" in why.lower()


def test_pass_contradicting_percent_threshold_is_refused(tmp_path):
    md = result("disk free", "pass", threshold="25%", comparator=">=", observed="6.1%")
    why = complete(board_with(tmp_path, BODY_PLAIN), "Disk work finished.", md)
    assert why and "guessed pass" in why.lower()


def test_pass_in_other_units_is_refused(tmp_path):
    md = result("disk free", "pass", threshold="25%", comparator=">=", observed="15 GB")
    why = complete(board_with(tmp_path, BODY_PLAIN), "Disk work finished.", md)
    assert why and "units" in why.lower()


def test_pass_without_observed_is_refused(tmp_path):
    why = complete(board_with(tmp_path, BODY_PLAIN), "Done.", result("check 4", "pass", threshold="80%"))
    assert why and "observed" in why.lower()


def test_pass_consistent_with_threshold_is_allowed(tmp_path):
    md = {"acceptance_results": [
        {"check": "check 4", "status": "pass", "threshold": "80%", "comparator": "<=", "observed": "78.5%"},
        {"check": "5b", "status": "pass", "threshold": 800, "comparator": "<", "observed": 712.4},
        {"check": "build", "status": "pass", "observed": "CI run 43161163808 success"},
    ]}
    assert complete(board_with(tmp_path, BODY_PLAIN), "All checks pass.", md) is None


def test_fail_without_preregistered_branch_is_refused(tmp_path):
    md = result("check 4", "fail", threshold="80%", comparator="<=", observed="82%")
    why = complete(board_with(tmp_path, BODY_PLAIN), "Reported check 4 as FAIL.", md)
    assert why and "IF FAIL" in why and "kanban_block" in why


def test_fail_with_preregistered_branch_is_allowed(tmp_path):
    md = result("check 4", "fail", threshold="80%", comparator="<=", observed="82%",
                branch="IF FAIL check 4: report FAIL, keep the source out of the next phase, file a decision card")
    assert complete(board_with(tmp_path, BODY_BRANCHED), "Reported check 4 as FAIL per its branch.", md) is None


def test_fail_must_cite_the_branch(tmp_path):
    md = result("check 4", "fail", threshold="80%", comparator="<=", observed="82%")
    why = complete(board_with(tmp_path, BODY_BRANCHED), "Reported check 4 as FAIL.", md)
    assert why and "branch" in why.lower()


def test_branch_for_another_check_does_not_cover(tmp_path):
    md = result("check 5", "fail", observed="3 of 20 seeds", branch="IF FAIL check 4")
    why = complete(board_with(tmp_path, BODY_BRANCHED), "Check 5 failed.", md)
    assert why and "check 5" in why


def test_waiver_shaped_branch_is_not_a_branch(tmp_path):
    md = result("check 4", "fail", observed="82%", branch="IF FAIL check 4")
    why = complete(board_with(tmp_path, BODY_WAIVER_BRANCH), "Check 4 failed; followed its branch.", md)
    assert why and "waive" in why.lower()


def test_not_run_is_treated_like_fail(tmp_path):
    why = complete(board_with(tmp_path, BODY_PLAIN), "Ran what I could.", result("check 2", "not_run"))
    assert why and "check 2" in why


def test_waived_without_body_waiver_is_refused(tmp_path):
    md = result("check 4", "waived", threshold="80%", observed="82%")
    why = complete(board_with(tmp_path, BODY_PLAIN), "Energy filter finished.", md)
    assert why and "waive" in why.lower() and "user message" in why


def test_waived_needs_body_waiver_naming_that_check(tmp_path):
    md = result("check 1", "waived", observed="culler +9%")
    why = complete(board_with(tmp_path, BODY_HUMAN_WAIVER), "Energy filter finished.", md)
    assert why and "check 1" in why


def test_waived_with_body_waiver_naming_check_is_allowed(tmp_path):
    md = result("check 4", "waived", threshold="80%", observed="82%")
    assert complete(board_with(tmp_path, BODY_HUMAN_WAIVER), "Energy filter finished.", md) is None


def test_unknown_status_is_refused(tmp_path):
    why = complete(board_with(tmp_path, BODY_PLAIN), "Done.", result("check 4", "pass-ish", observed="81%"))
    assert why and "status" in why.lower()


def test_metadata_as_json_string(tmp_path):
    md = json.dumps(result("5b", "pass", threshold=800, comparator="<", observed=807.35))
    why = complete(board_with(tmp_path, BODY_PLAIN), "Oracle checks done.", md)
    assert why and "guessed pass" in why.lower()


def test_other_cards_are_not_gated(tmp_path):
    args = {"task_id": "t_ffffffffffffffff", "summary": "Check 4 failed; waived it."}
    assert plugin.classify("kanban_complete", args, OWN, board_with(tmp_path, BODY_PLAIN)) is None


def test_hook_blocks_in_worker(tmp_path, monkeypatch):
    paths_fn = board_with(tmp_path, BODY_PLAIN)
    monkeypatch.setenv("HERMES_KANBAN_TASK", OWN)
    monkeypatch.setattr(core, "board_paths", lambda env=None: paths_fn())
    out = plugin._pre_tool_call("kanban_complete", {"summary": "Check 4 failed; waived it as an artefact."})
    assert out and out["action"] == "block" and "threshold" in out["message"].lower()


# --- independent review findings (deleg_2aadb670) -----------------------------------------------------------------

@pytest.mark.parametrize("entry", [
    {"check": "oracle", "status": "pass", "threshold": 800, "observed": 790},          # no comparator
    {"check": "oracle", "status": "pass", "threshold": ">= 800", "observed": 790},     # comparator in the threshold
    {"check": "oracle", "status": "pass", "threshold": ">= 800", "observed": "n/a"},   # no evidence
    {"check": "disk", "status": "pass", "threshold": ">= 25% free", "observed": "6.1% free"},
])
def test_structured_pass_review_shapes_refused(tmp_path, entry):
    assert complete(board_with(tmp_path, BODY_PLAIN), "Done.", {"acceptance_results": [entry]})


@pytest.mark.parametrize("entry", [
    {"check": "oracle", "status": "pass", "threshold": ">= 800", "observed": 812},
    {"check": "disk", "status": "pass", "threshold": "25% free", "comparator": ">=", "observed": "31% free"},
])
def test_structured_pass_review_shapes_allowed(tmp_path, entry):
    assert complete(board_with(tmp_path, BODY_PLAIN), "Done.", {"acceptance_results": [entry]}) is None


def test_prose_inside_acceptance_results_is_scanned(tmp_path):
    md = result("oracle", "pass", threshold=">= 800", observed=812, note="5b missed the bar, treated as a pass")
    why = complete(board_with(tmp_path, BODY_PLAIN), "Done.", md)
    assert why and "guessed pass" in why


BODY_WAIVER_CHECK1 = BODY_PLAIN + "\nWAIVED: check 1 per user message 4567\n"


@pytest.mark.parametrize("name", ["4", "check", "check 14"])
def test_waived_label_must_match_whole(tmp_path, name):
    assert complete(board_with(tmp_path, BODY_WAIVER_CHECK1), "Done.", result(name, "waived", observed="x"))


def test_prose_waiver_exemption_is_per_check(tmp_path):
    paths_fn = board_with(tmp_path, BODY_WAIVER_CHECK1)
    assert complete(paths_fn, "Check 4 failed (82%) and is waived.")
    assert complete(paths_fn, "Check 1 failed (+9%) and is waived.") is None


BODY_CHECK1_BRANCH = BODY_PLAIN + "\nIF FAIL check 1: re-run once on fresh seeds, then report FAIL and stop.\n"


def test_branch_label_must_match_whole(tmp_path):
    md = result("check 14", "fail", observed="x", branch="IF FAIL check 1")
    assert complete(board_with(tmp_path, BODY_CHECK1_BRANCH), "Check 14 failed.", md)


@pytest.mark.parametrize("cite", ["r", "if fail", "IF FAIL:", "check"])
def test_branch_cite_must_name_label_or_step(tmp_path, cite):
    md = result("check 1", "fail", observed="x", branch=cite)
    assert complete(board_with(tmp_path, BODY_CHECK1_BRANCH), "Check 1 failed per its branch.", md)


@pytest.mark.parametrize("summary", [
    "Check 4 failed. Waived.",
    "Treated check 4 as passing.",
    "Check 4 failed, but this is acceptable.",
    "Check 4 failed at 82%; deemed acceptable for this phase.",
    # re-review (deleg_caaf2102)
    "Check 4 failed at 82%. That is acceptable for this phase.",
    "Check 4 failed at 82%.\nThat is acceptable for this phase.",
    "Check 4 failed (82% vs 80%). It's waived for this phase.",
    "Disk freed to 6.1% (target 25% missed). Accepted.",
    "Disk 6.1% free (target 25% missed). Accepting as is.",
    "Check 4 missed by 2 points; we consider it passed.",
    "Oracle 807.35 vs < 800 target; passes within tolerance.",
])
def test_review_prose_misses_now_refused(tmp_path, summary):
    assert complete(board_with(tmp_path, BODY_PLAIN), summary)


@pytest.mark.parametrize("summary", [
    "3 tests failed before the fix. Waived nothing; all pass now.",
    "The float comparison test passes within tolerance 1e-6.",
    "Tests failed before the change and pass after it.",
    # delta re-review (deleg_070e3219)
    "Fixed the test that fails on CI. Accepted the reviewer suggestion.",
    "The flaky test failed twice on CI. Accepted the PR review comments and merged.",
    "Old code failed on empty lists. Acceptable inputs now include empty lists.",
    "Previously, requests exceeding the limit failed. Accepted requests now return 202.",
    "Login failed for expired tokens (expected). That is accepted behavior per spec.",
    "Added `is_passing()`; we consider it passing when exit code is 0.",
    "The rate limiter test passes within tolerance of the limit.",
    "Benchmark passes within the noise threshold we documented (all 5 runs under target).",
])
def test_review_prose_honest_allowed(tmp_path, summary):
    assert complete(board_with(tmp_path, BODY_PLAIN), summary) is None


@pytest.mark.parametrize("step", ["complete the card anyway", "accept the result and complete", "mark the card done"])
def test_more_waiver_shaped_branches(tmp_path, step):
    md = result("check 4", "fail", observed="82%", branch="IF FAIL check 4")
    assert complete(board_with(tmp_path, BODY_PLAIN + f"\nIF FAIL check 4: {step}\n"), "Check 4 failed.", md)


def test_cli_request_review_is_checked(tmp_path):
    cmd = f"hermes kanban request-review {OWN} --summary 'Check 4 failed at 82%; waived it.'"
    assert plugin.classify("terminal", {"command": cmd}, OWN, board_with(tmp_path, BODY_PLAIN))


def test_gate_g_crash_does_not_skip_other_gates(tmp_path, monkeypatch):
    mod = core._thresholds_mod()

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(mod, "check_thresholds", boom)
    paths_fn = board_with(tmp_path, BODY_PLAIN)
    assert complete(paths_fn, "All done.") is None
    assert complete(paths_fn, "I did not run the live check.")  # Gate B still refuses
