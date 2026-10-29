"""A task's defect-description title is context, not a current handoff admission."""

import shlex

import pytest

from test_honest_done import OWN, core, make_board, plugin


TITLE = "Reap terminal scratch workspaces whose completion-time cleanup was deferred (no retry exists)"
SUMMARY = "Implemented and published the guarded terminal scratch retry extension; all acceptance tests passed."


def completion_args(channel, summary, metadata):
    args = {"summary": summary, "metadata": metadata}
    if channel == "kanban_complete":
        return args
    if channel == "tool_call":
        return {"calls": [{"name": "kanban_complete", "arguments": args}]}
    import json
    command = (f"hermes kanban complete {OWN} --summary {shlex.quote(summary)} "
               f"--metadata {shlex.quote(json.dumps(metadata))}")
    if channel == "terminal":
        return {"command": command}
    return {"code": f'terminal("""{command}""")'}


@pytest.mark.parametrize("channel", ["kanban_complete", "tool_call", "terminal", "execute_code"])
@pytest.mark.parametrize("admission", [None, "summary", "metadata"])
def test_defect_title_does_not_override_current_handoff(tmp_path, channel, admission):
    db = make_board(tmp_path, tasks=[(OWN, TITLE, "running")])
    summary = SUMMARY
    metadata: dict[str, object] = {"tests": "All regression tests passed."}
    if admission == "summary":
        summary += " The retry implementation remains deferred."
    if admission == "metadata":
        metadata["acceptance"] = {"remaining": ["The retry implementation remains deferred."]}
    why = plugin.classify(channel, completion_args(channel, summary, metadata), OWN, lambda: [db])
    if admission:
        assert why and "Refused" in why and "retry implementation remains deferred" in why
    else:
        assert why is None


def test_explicit_title_context_not_an_admission():
    assert core.check_complete(OWN, SUMMARY, [], title=TITLE) is None


@pytest.mark.parametrize("unfinished", [False, True])
def test_sidebar_uses_current_handoff_not_defect_title(tmp_path, unfinished):
    db = make_board(tmp_path, tasks=[(OWN, TITLE, "done")])
    summary = "The retry implementation remains deferred." if unfinished else SUMMARY
    assert core.needs_attention({"id": OWN, "title": TITLE, "summary": summary}, [db]) is unfinished
