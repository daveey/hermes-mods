"""`hermes kanban comment` arguments read as hermes's own parser reads them.

argparse accepts any unique option prefix (`--max 5` is `--max-len 5`, `--boa x` is `--board x`), `--opt=value`,
and options after the text. The guard must read the same task id, text and board, with core's parser when
core imports and with the static reader in no-core runs.
"""
import argparse
import importlib.util
import shlex
import sqlite3

import pytest

from test_honest_done import plugin, core, make_board, OWN

BRANCH = "IF FAIL cpu: post the table, then request review."
cb = plugin.comment_branches
HAVE_CORE = importlib.util.find_spec("hermes_cli") is not None and \
    importlib.util.find_spec("hermes_cli.kanban_parser") is not None

FORMS = [
    "--max 5 {id} {text}",
    "--max=5 {id} {text}",
    "--auth bob {id} {text}",
    "--origin worker-3 {id} {text}",
    "{id} {text} --max 5",
]


@pytest.fixture
def body(tmp_path, monkeypatch):
    db = make_board(tmp_path, tasks=[(OWN, "test", "running")])
    monkeypatch.setattr(core, "board_paths", lambda: [db])
    monkeypatch.setattr(cb, "target_paths", lambda board: [db])
    monkeypatch.setattr(plugin, "classify_consent", lambda *a, **kw: None)
    monkeypatch.setattr(plugin, "_in_worker", lambda: False)

    def set_body(text):
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE tasks SET body=?", (text,))
    return set_body


@pytest.fixture(params=["core", "static"])
def reader(request, monkeypatch):
    """Run each case through core's parser (when core imports) and through the no-core static reader."""
    if request.param == "core" and not HAVE_CORE:
        pytest.skip("core isn't importable in this run (no-core)")
    if request.param == "static":
        monkeypatch.setattr(cb, "core_parser", lambda: None, raising=False)
    return request.param


def guard(command):
    return plugin._pre_tool_call("terminal", {"command": command})


def comment(form, text=BRANCH, kanban="kanban"):
    return f"hermes {kanban} comment " + form.format(id=OWN, text=shlex.quote(text))


@pytest.mark.parametrize("form", FORMS)
def test_unregistered_branch_is_refused_whatever_the_option_spelling(body, reader, form):
    body("")
    result = guard(comment(form))
    assert result and result["action"] == "block"
    assert "An IF FAIL branch counts only in the card body" in result["message"]


@pytest.mark.parametrize("form", FORMS)
def test_registered_branch_is_allowed_whatever_the_option_spelling(body, reader, form):
    # An option value read as text ("... review. 5") would change the step and refuse a registered branch.
    body(BRANCH)
    assert guard(comment(form)) is None


@pytest.mark.parametrize("kanban", ["kanban --boa selected", "kanban --b selected", "kanban --boar=selected"])
def test_board_prefix_selects_the_board(body, reader, monkeypatch, kanban):
    paths = cb.target_paths(None)
    seen = []

    def resolve(board):
        seen.append(board)
        return paths
    monkeypatch.setattr(cb, "target_paths", resolve)
    body("")
    assert guard(comment("{id} {text}", kanban=kanban))["action"] == "block"
    assert seen == ["selected"]


@pytest.mark.parametrize("form", ["--help {id} {text}", "--he {id} {text}", "-h {id} {text}", "{id} {text} --h"])
def test_help_posts_nothing_so_nothing_is_refused(body, reader, form):
    body("")
    assert guard(comment(form)) is None


def test_core_reader_rejects_what_hermes_rejects(body):
    """`--max-len` takes an int: hermes refuses `--max abc`, posts nothing, so the guard has nothing to refuse."""
    if not HAVE_CORE:
        pytest.skip("core isn't importable in this run (no-core)")
    body("")
    assert guard(comment("--max abc {id} {text}")) is None
    assert guard(comment("--max 7 {id} {text}"))["action"] == "block"


def test_core_reader_does_not_lean_on_the_static_lists(body, monkeypatch):
    """With core importable, its parser decides; emptied static lists must not reopen the hole."""
    if not HAVE_CORE:
        pytest.skip("core isn't importable in this run (no-core)")
    monkeypatch.setattr(cb, "COMMENT_VALUE_OPTIONS", (), raising=False)
    monkeypatch.setattr(cb, "KANBAN_VALUE_OPTIONS", (), raising=False)
    body("")
    assert guard(comment("--max 5 {id} {text}"))["action"] == "block"
    assert guard(comment("--auth bob {id} {text}"))["action"] == "block"


def _long_options(parser):
    values, flags = set(), set()
    for action in parser._actions:
        longs = {o for o in action.option_strings if o.startswith("--")}
        (flags if action.nargs == 0 else values).update(longs)
    return values, flags


def _sub(parser, name):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[name]
    raise AssertionError(f"no {name} subcommand")


def test_static_lists_match_core_parser():
    """Drift check: the no-core reader's option lists are exactly the options core's parser defines."""
    if not HAVE_CORE:
        pytest.skip("core isn't importable in this run (no-core)")
    root = cb.core_parser()
    assert root is not None
    kanban = _sub(root, "kanban")
    values, flags = _long_options(_sub(kanban, "comment"))
    assert values == set(cb.COMMENT_VALUE_OPTIONS)
    assert flags == set(cb.COMMENT_FLAG_OPTIONS)
    assert _long_options(kanban) == (set(cb.KANBAN_VALUE_OPTIONS), set(cb.COMMENT_FLAG_OPTIONS))


def test_core_parser_is_quiet(capsys):
    """Help and usage errors raise inside the hook; nothing is printed to the agent's stdout or stderr."""
    if not HAVE_CORE:
        pytest.skip("core isn't importable in this run (no-core)")
    root = cb.core_parser()
    for argv in (["kanban", "comment", "--help"], ["kanban", "comment", "--max", "abc", OWN, "x"],
                 ["kanban", "--help"], ["kanban", "comment", "--bogus", OWN, "x"]):
        with pytest.raises(cb._NoWrite):
            root.parse_args(argv)
    assert capsys.readouterr() == ("", "")
