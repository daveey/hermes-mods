"""Early feedback for branch-looking comments; Gate G still reads only bodies."""
import re
from pathlib import Path


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
            rest = parsed[1]
            # CLI: comment ID TEXT [TEXT ...], with value-taking metadata options.
            positional = []
            i = 0
            while i < len(rest):
                token = rest[i]
                if token in {"--author", "--max-len", "--origin-gateway"}:
                    i += 2
                    continue
                if token == "--":
                    positional.extend(rest[i + 1:])
                    break
                if token.startswith("--"):
                    i += 1
                    continue
                positional.append(token)
                i += 1
            if len(positional) >= 2:
                yield positional[0], " ".join(positional[1:]), core._opt(seg, "--board", "-b"), False


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
