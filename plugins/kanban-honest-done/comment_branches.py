"""Early feedback for branch-looking comments; Gate G still reads only bodies."""
import os
import re
from pathlib import Path

# `hermes kanban [--board B] comment TASK_ID TEXT...` options as core defines them (hermes_cli/kanban_parser.py).
# argparse accepts any unique prefix (`--max 5` is `--max-len 5`), so the reader resolves prefixes too.
# The hook never builds core's parser: the first build in a process costs 63-107 ms (t_01a1dee4d010c1ba).
# tests/test_comment_branches_cli_options.py checks these lists, and what the reader returns, against core's
# parser whenever core imports.
COMMENT_VALUE_OPTIONS = ("--author", "--max-len", "--origin-gateway")
COMMENT_FLAG_OPTIONS = ("--help",)
KANBAN_VALUE_OPTIONS = ("--board",)


def _resolve(token, options):
    """argparse's reading of `--name[=value]`: (option, inline value or None), ("", None) if ambiguous, else None."""
    name, eq, value = token.partition("=")
    inline = value if eq else None
    if name in options:
        return name, inline
    hits = [o for o in options if o.startswith(name)]
    if len(hits) > 1:
        return "", None
    return (hits[0], inline) if hits else None


# Core's top-level flags that take a value (hermes_cli/_parser.py `top_level_value_flag_sets()` plus the pre-argparse
# `-p/--profile`). Static so hook startup imports no core; tests/test_top_level_flags.py drift-checks it against core.
TOP_LEVEL_VALUE_FLAGS = frozenset({
    "-p", "--profile", "-z", "--oneshot", "-m", "--model", "--provider", "--reasoning", "-t", "--toolsets",
    "-r", "--resume", "-s", "--skills", "--usage-file", "--in", "--cpu-profile",
})
TOP_LEVEL_OPTIONAL_VALUE_FLAGS = frozenset({"-c", "--continue"})


def kanban_indexes(seg):
    """Indexes k where seg is `hermes [TOP-LEVEL FLAGS] kanban` with `kanban` at seg[k]."""
    for h, tok in enumerate(seg):
        real = os.path.basename(tok) in ("hermes", "hermes.exe")
        if not (real or tok.endswith("hermes_cli.main")):
            continue
        j = h + 1
        while j < len(seg) and seg[j].startswith("-"):
            if seg[j] == "--":
                # Core's command_argv consumes this marker, then dispatches the next token.
                j += 1
                break
            j += 2 if "=" not in seg[j] and seg[j] in TOP_LEVEL_VALUE_FLAGS else 1
        if j < len(seg) and (seg[j] == "kanban" or (real and os.path.basename(seg[j]) == "kanban")):
            yield j


def _kanban_options(seg, rest):
    """Tokens between `hermes kanban` and the verb: the kanban-level options (`--board`)."""
    end = len(seg) - len(rest) - 1
    for k in kanban_indexes(seg):
        if k < end:
            return seg[k + 1:end]
    return []


def _read_comment(kanban_opts, rest):
    """Static reader of core's grammar: unique prefixes, `--opt=value`, options before or after the text."""
    board, i = None, 0
    while i < len(kanban_opts):
        token = kanban_opts[i]
        hit = _resolve(token, KANBAN_VALUE_OPTIONS + COMMENT_FLAG_OPTIONS) if token.startswith("--") and len(token) > 2 else None
        if token == "-h" or (hit and hit[0] in ("", "--help")):
            return None
        if hit or token == "-b":
            if hit and hit[1] is not None:
                board, i = hit[1], i + 1
            else:
                board, i = (kanban_opts[i + 1] if i + 1 < len(kanban_opts) else None), i + 2
            continue
        i += 1
    positional, i = [], 0
    while i < len(rest):
        token = rest[i]
        if token == "--":
            positional.extend(rest[i + 1:])
            break
        if token == "-h":
            return None
        if token.startswith("--"):
            hit = _resolve(token, COMMENT_VALUE_OPTIONS + COMMENT_FLAG_OPTIONS)
            if hit is not None:
                if hit[0] in ("", "--help"):
                    return None  # help or an ambiguous option: hermes posts nothing
                i += 1 if hit[1] is not None else 2
                continue
            if " " not in token:
                i += 1  # an option hermes doesn't know refuses the command; skipping it keeps the check closed
                continue
        positional.append(token)
        i += 1
    if len(positional) < 2:
        return None
    return positional[0], " ".join(positional[1:]).strip(), board


def comment_args(seg, rest):
    """(task id, text, board) as hermes reads `... kanban [OPTS] comment REST`, or None when it posts nothing."""
    return _read_comment(_kanban_options(seg, rest), rest)


def hint(task_id):
    return ("An IF FAIL branch counts only in the card body, because Gate G reads the body. "
            f"Add it with `hermes kanban edit {task_id} --body ...` on the card's home gateway, then comment. "
            "If you didn't mean to register a branch, write it as plain prose, for example "
            "'On failure, quote the failing test and diagnose it.'")


def target_paths(board):
    # Resolve the actual write destination, including current-board and worker pins.
    # Never borrow a same-ID row from another board to decide a refusal.
    from hermes_cli.kanban_db import kanban_db_path
    return [Path(kanban_db_path(board))]


def writes(name, args, core):
    """Yield (id, text, board, foreign) for literal supported comment writes."""
    name = str(name or "").removeprefix("mcp__")
    args = args if isinstance(args, dict) else {}
    if name == "tool_call":
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                yield from writes(call.get("name"), call.get("arguments"), core)
    elif name == "kanban_comment":
        yield str(args.get("task_id") or ""), str(args.get("body") or ""), args.get("board"), False
    elif name == "cross_task" and args.get("action") == "comment":
        target = str(args.get("target") or "")
        board, foreign = args.get("board"), False
        if target.startswith("@task:") and "/" in target:
            scope, target = target[6:].split("/", 1)
            foreign = "@" in scope
            board = scope.split("@", 1)[0]
        elif ":" in target:
            foreign = True
            target = target.rsplit(":", 1)[-1]
        yield target, str(args.get("message") or ""), board, foreign
    elif name == "terminal":
        for seg in core._segments(str(args.get("command") or "")):
            parsed = core._kanban_verb(seg)
            if not parsed or parsed[0] != "comment":
                continue
            # Read the arguments the way hermes's argparse does: `--max 5` is `--max-len 5`, and options may
            # follow the text. A guess here hides the card (fail-open) or adds a value to the text.
            hit = comment_args(seg, parsed[1])
            if hit:
                yield (*hit, False)


def check(name, args, core):
    """Return (refusal, unreadable hints). Unknown targets are never refused blind."""
    thresholds = core._load_sibling("thresholds")
    hints = []
    for task_id, text, board, foreign in writes(name, args, core):
        branches = thresholds.preregistered_branches(text)
        if not branches:
            continue
        found = None
        if not foreign and re.fullmatch(r"t_[A-Za-z0-9]+", task_id):
            try:
                found = core.find_task(task_id, target_paths(board))
            except Exception:
                pass  # unavailable/missing/foreign body: advise, never refuse blind
        message = hint(task_id or "<id>")
        if found is None:
            hints.append(message)
            continue
        registered = {step for _, step in thresholds.preregistered_branches(found[1].get("body") or "")}
        if any(step not in registered for _, step in branches):
            return message, hints
    return None, list(dict.fromkeys(hints))
