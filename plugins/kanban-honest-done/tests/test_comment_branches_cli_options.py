"""`hermes kanban comment` arguments read as hermes's own parser reads them.

argparse accepts any unique option prefix (`--max 5` is `--max-len 5`, `--boa x` is `--board x`), `--opt=value`,
and options after the text. The guard reads the same task id, text and board with a static reader, in core-backed
and no-core runs alike. It never builds core's parser inside the hook: the first build in a process costs 63-107 ms.
When core imports, the drift checks below compare the static option lists, and what the reader returns, with core's
real parser.
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
needs_core = pytest.mark.skipif(not HAVE_CORE, reason="core isn't importable in this run (no-core)")

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


def guard(command):
    return plugin._pre_tool_call("terminal", {"command": command})


def comment(form, text=BRANCH, kanban="kanban"):
    return f"hermes {kanban} comment " + form.format(id=OWN, text=shlex.quote(text))


@pytest.mark.parametrize("form", FORMS)
def test_unregistered_branch_is_refused_whatever_the_option_spelling(body, form):
    body("")
    result = guard(comment(form))
    assert result and result["action"] == "block"
    assert "An IF FAIL branch counts only in the card body" in result["message"]


@pytest.mark.parametrize("form", FORMS)
def test_registered_branch_is_allowed_whatever_the_option_spelling(body, form):
    # An option value read as text ("... review. 5") would change the step and refuse a registered branch.
    body(BRANCH)
    assert guard(comment(form)) is None


@pytest.mark.parametrize("kanban", ["kanban --boa selected", "kanban --b selected", "kanban --boar=selected"])
def test_board_prefix_selects_the_board(body, monkeypatch, kanban):
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
def test_help_posts_nothing_so_nothing_is_refused(body, form):
    body("")
    assert guard(comment(form)) is None


def test_hook_builds_no_argument_parser(body, monkeypatch):
    """The cold-start fix: a comment check constructs no argparse parser, core's or any other."""
    for value in vars(cb).values():  # a parser cached by an earlier test would hide a build
        if callable(getattr(value, "cache_clear", None)):
            value.cache_clear()
    built = []
    original = argparse.ArgumentParser.__init__

    def record(self, *args, **kwargs):
        built.append(kwargs.get("prog"))
        original(self, *args, **kwargs)
    monkeypatch.setattr(argparse.ArgumentParser, "__init__", record)
    body("")
    assert guard(comment("--max 5 {id} {text}"))["action"] == "block"
    assert guard(comment("--origin w {id} {text}", kanban="kanban --boa=x"))["action"] == "block"
    assert built == []


# ---- drift checks against core's real parser (core-backed runs only) ----

class _NoWrite(Exception):
    """hermes would print help or a usage error for these arguments, so it posts nothing."""


class _Help(_NoWrite):
    pass


class _QuietParser(argparse.ArgumentParser):
    # Subparsers inherit the root's class: help and usage errors raise here instead of printing and exiting.
    def _print_message(self, message, file=None):
        pass

    def exit(self, status=0, message=None):
        raise _Help()  # only help exits without error() here

    def error(self, message):
        raise _NoWrite()


def _core_root():
    from hermes_cli.kanban_parser import build_parser
    root = _QuietParser(prog="hermes", add_help=False)
    build_parser(root.add_subparsers(dest="command"))
    return root


def _options(parser):
    # Every option string, short ones included: a new `-a` alias in core would otherwise pass this check while
    # the reader takes `-a` for the task id and fails open (review of PR #251).
    values, flags = set(), set()
    for action in parser._actions:
        if action.option_strings:
            # The reader consumes exactly one required value, not ?, *, + or a list-valued integer arity.
            assert action.nargs is None or action.nargs == 0, (action.option_strings, action.nargs)
        (flags if action.nargs == 0 else values).update(action.option_strings)
    return values, flags


def _sub(parser, name):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[name]
    raise AssertionError(f"no {name} subcommand")


@needs_core
def test_static_lists_match_core_parser():
    """Drift check: the reader's option lists are exactly the options core's parser defines."""
    kanban = _sub(_core_root(), "kanban")
    help_flags = {*cb.COMMENT_FLAG_OPTIONS, "-h"}  # the reader handles `-h` by its exact token
    assert _options(_sub(kanban, "comment")) == (set(cb.COMMENT_VALUE_OPTIONS), help_flags)
    assert _options(kanban) == (set(cb.KANBAN_VALUE_OPTIONS), help_flags)


@needs_core
@pytest.mark.parametrize("nargs", ["?", "*", "+", 1, 2])
def test_drift_check_rejects_changed_value_arity(monkeypatch, nargs):
    """Core's option arity must not silently change how the static reader consumes tokens."""
    root = _core_root()
    parser = _sub(_sub(root, "kanban"), "comment")
    action = parser._option_string_actions["--origin-gateway"]
    action.nargs = nargs
    if nargs == "?":
        # The previously surviving mutant accepts this but the reader consumes --author as the origin value.
        rest = ["--origin-gateway", "--author", "bob", OWN, BRANCH]
        ns = root.parse_args(["kanban", "comment", *rest])
        assert ns.task_id == OWN and ns.text == [BRANCH]
        assert cb.comment_args(["hermes", "kanban", "comment", *rest], rest) != (OWN, BRANCH, ns.board)
    monkeypatch.setitem(globals(), "_core_root", lambda: root)
    with pytest.raises(AssertionError):
        test_static_lists_match_core_parser()


KANBAN_OPTS = [[], ["--board", "b1"], ["--boa", "b1"], ["--b=b1"]]
RESTS = [
    [OWN, "hello", "world"],
    ["--max", "5", OWN, BRANCH],
    ["--max=5", OWN, BRANCH],
    ["--m", "5", OWN, BRANCH],
    ["--auth", "bob", OWN, BRANCH],
    ["--a=bob", OWN, BRANCH],
    ["--origin", "worker-3", OWN, BRANCH],
    ["--o", "worker-3", OWN, BRANCH],
    [OWN, BRANCH, "--max", "5"],
    [OWN, "--author", "x", "IF FAIL a:", "b"],
    [OWN, "one", "two", "--origin-gateway=g"],
    [OWN, "one", "--origin-gateway=g", "two"],  # core: usage error (text split by an option)
    ["--max", "abc", OWN, BRANCH],  # core: usage error (not an int)
    ["--help", OWN, BRANCH],
    ["--he", OWN, BRANCH],
    [OWN, BRANCH, "-h"],
]


@needs_core
@pytest.mark.parametrize("kanban_opts", KANBAN_OPTS, ids=lambda o: " ".join(o) or "no-board")
@pytest.mark.parametrize("rest", RESTS, ids=lambda r: " ".join(r)[:40])
def test_reader_agrees_with_core_parser(kanban_opts, rest):
    """Drift check: where core's parser accepts the command, the reader returns its task id, text and board;
    where core prints help, the reader sees no write. On a usage error hermes posts nothing, so the reader may
    read a write there (a refusal of a command hermes rejects anyway: over-refusal, never fail-open)."""
    seg = ["hermes", "kanban", *kanban_opts, "comment", *rest]
    try:
        ns = _core_root().parse_args(["kanban", *kanban_opts, "comment", *rest])
    except _Help:
        assert cb.comment_args(seg, rest) is None
        return
    except _NoWrite:
        return
    assert cb.comment_args(seg, rest) == (str(ns.task_id), " ".join(ns.text).strip(), ns.board)
