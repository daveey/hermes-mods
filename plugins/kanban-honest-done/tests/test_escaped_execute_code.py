"""execute_code must see the shell command a Python literal would pass, not its source spelling.

A worker builds ``code = f"terminal({command!r})"`` with ``shlex.quote`` around the summary and
metadata. That source still contains ``\\'`` escapes. Scanning the source text misses the admission;
decoding the literal (without running the snippet) must refuse it. Triple quotes already did.
"""

import ast
import json
import shlex

import pytest

from test_honest_done import OWN, core, make_board, plugin

SUMMARY_CLEAN = "Implemented retry. All acceptance checks passed."
SUMMARY_DROP = "Implemented retry. The retry implementation remains deferred."
META_CLEAN = {"tests": "All regression tests passed."}
META_DROP = {"acceptance": {"remaining": ["The retry implementation remains deferred."]}}


def _command(summary, metadata):
    return (
        f"hermes kanban complete {OWN} --summary {shlex.quote(summary)} "
        f"--metadata {shlex.quote(json.dumps(metadata))}"
    )


def _execute_code(command, style):
    if style == "repr":
        return f"terminal({command!r})"
    if style == "triple":
        return f'terminal("""{command}""")'
    raise AssertionError(style)


@pytest.mark.parametrize("style", ["repr", "triple"])
@pytest.mark.parametrize("where", ["summary", "metadata", "clean"])
def test_decoded_execute_code_completion(tmp_path, style, where):
    db = make_board(tmp_path, tasks=[(OWN, "retry extension", "running")])
    summary = SUMMARY_DROP if where == "summary" else SUMMARY_CLEAN
    metadata = META_DROP if where == "metadata" else META_CLEAN
    code = _execute_code(_command(summary, metadata), style)
    if style == "repr":
        assert "\\'" in code  # the bug is this spelling, not an already-decoded command
    why = plugin.classify("execute_code", {"code": code}, OWN, lambda: [db])
    if where == "clean":
        assert why is None
    else:
        assert why and "Refused" in why and "retry implementation remains deferred" in why


def test_repr_apostrophe_summary_still_refused(tmp_path):
    db = make_board(tmp_path, tasks=[(OWN, "retry extension", "running")])
    summary = "The worker's retry implementation remains deferred."
    code = f"terminal({_command(summary, META_CLEAN)!r})"
    assert "\\'" in code
    why = plugin.classify("execute_code", {"code": code}, OWN, lambda: [db])
    assert why and "Refused" in why and "remains deferred" in why


def test_multiline_triple_quoted_summary_refused(tmp_path):
    db = make_board(tmp_path, tasks=[(OWN, "retry extension", "running")])
    code = 'terminal("""\n' + _command(SUMMARY_DROP, META_CLEAN) + '\n""")\n'
    why = plugin.classify("execute_code", {"code": code}, OWN, lambda: [db])
    assert why and "Refused" in why and "retry implementation remains deferred" in why


def test_concatenated_literals_are_one_command(tmp_path):
    db = make_board(tmp_path, tasks=[(OWN, "retry extension", "running")])
    code = (
        f"terminal('hermes kanban complete {OWN} --summary ' "
        "'\"The retry implementation remains deferred.\"')"
    )
    why = plugin.classify("execute_code", {"code": code}, OWN, lambda: [db])
    assert why and "Refused" in why


def test_repr_escaped_snippet_is_not_executed(tmp_path):
    db = make_board(tmp_path, tasks=[(OWN, "retry extension", "running")])
    code = "raise SystemExit('executed user code')\n" + f"terminal({_command(SUMMARY_DROP, META_CLEAN)!r})"
    why = plugin.classify("execute_code", {"code": code}, OWN, lambda: [db])
    assert why and "Refused" in why


def test_syntax_error_fallback_still_decodes_repr(tmp_path):
    db = make_board(tmp_path, tasks=[(OWN, "retry extension", "running")])
    code = f"terminal({_command(SUMMARY_DROP, META_CLEAN)!r}"  # missing closing paren
    with pytest.raises(SyntaxError):
        ast.parse(code)
    why = core.check_python(code, OWN, lambda: [db])
    assert why and "Refused" in why and "retry implementation remains deferred" in why


def test_own_edit_still_refused_when_repr_escaped(tmp_path):
    db = make_board(tmp_path, tasks=[(OWN, "retry extension", "running")])
    cmd = f"hermes kanban edit {OWN} --body {shlex.quote('dropped the iMac run')}"
    why = plugin.classify("execute_code", {"code": f"terminal({cmd!r})"}, OWN, lambda: [db])
    assert why and "Don't narrow" in why


def test_runtime_concatenation_is_not_executed(tmp_path):
    """An admission that exists only after the snippet runs must not be recovered by running it."""
    db = make_board(tmp_path, tasks=[(OWN, "retry extension", "running")])
    code = (
        "summary = 'The retry implementation remains deferred.'\n"
        f"terminal('hermes kanban complete {OWN} --summary ' + summary)\n"
    )
    assert plugin.classify("execute_code", {"code": code}, OWN, lambda: [db]) is None


def test_own_edit_still_refused_in_existing_escape_form(tmp_path):
    db = make_board(tmp_path, tasks=[(OWN, "retry extension", "running")])
    code = f"terminal(\"hermes kanban edit {OWN} --body 'dropped the iMac run'\")\n"
    why = plugin.classify("execute_code", {"code": code}, OWN, lambda: [db])
    assert why and "Don't narrow" in why
