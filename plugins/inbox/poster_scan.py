"""Static scan for Inbox poster labels across source trees (hermes-mods, daveey/hermes state, hermes-hub).

Used by tests/test_poster_conformance.py: every literal poster a call site passes must normalise to a
family in posters.yaml, so a new unregistered poster fails CI instead of reaching the live Inbox.
"""
from __future__ import annotations

import ast
from pathlib import Path

SKIP_DIRS = {"tests", "__pycache__", ".git", "node_modules", ".venv", "venv", "hermes-agent", "build", "dist"}
# Callee names whose ``poster=`` keyword (or ``--poster`` argv pair) feeds the Inbox.
POST_CALLEES = {"post", "inbox_post", "inbox", "post_inbox", "_post_inbox", "_inbox", "handle_post", "inbox_guard"}


def _callee(node: ast.Call) -> str:
    f = node.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _const(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def scan(root: Path) -> list[dict]:
    """Return ``{site, poster}`` for every literal poster handed to an inbox-posting call under ``root``."""
    out: list[dict] = []
    for path in sorted(Path(root).rglob("*.py")):
        rel = path.relative_to(root)
        # Nested agent worktrees are stale copies, not deployed producers.
        # Keep other .claude sources (e.g. hooks) in scope.
        nested_claude_tree = any(a == '.claude' and b == 'worktrees'
                                 for a, b in zip(rel.parts, rel.parts[1:]))
        if (any(p in SKIP_DIRS or p == '.worktrees' for p in rel.parts)
                or nested_claude_tree or rel.name.startswith("test_")):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        if "inbox" not in text.lower() and "poster" not in text:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                for kw in n.keywords:
                    if kw.arg == "poster" and (_const(kw.value) is not None) and (
                            _callee(n) in POST_CALLEES or "inbox" in text.lower()):
                        out.append({"site": f"{rel}:{n.lineno}", "poster": _const(kw.value)})
            elif isinstance(n, (ast.List, ast.Tuple)):
                items = n.elts
                for i, el in enumerate(items[:-1]):
                    if _const(el) == "--poster" and _const(items[i + 1]) is not None:
                        out.append({"site": f"{rel}:{n.lineno}", "poster": _const(items[i + 1])})
    return out


# --- Inbox posts that name no poster ----------------------------------------------------------------------------
# A keyed post with no poster gets the derived poster "key · <prefix>" (store.key_poster), whose family "key" is never
# registered: the gate refuses it on every run (cron-3299286b9a). scan() only sees literal posters, so it cannot flag
# a call that names none. scan_missing() does, for the three ways Python code reaches the Inbox:
#   argv     ["hermes", "inbox", "post", ...] with no "--poster" in the list or the enclosing function
#   payload  an Inbox payload dict literal ({"key", "type", "title", ...}) without a "poster" key
#   call     inbox.post(..., key=...) / inbox_guard.post(payload) whose enclosing function never mentions a poster
# "Mentions a poster" is deliberately coarse: a transport that forwards the caller's poster (inbox_guard) or a
# wrapper that sets it via dict(card, poster=...) is fine; only a function with no poster anywhere is flagged.

_PAYLOAD_KEYS = {"key", "type", "title"}
_PAYLOAD_BODY = {"fields", "body"}
_SNAPSHOT_KEYS = {"session_id", "state", "outcome"}  # an outcome/readback snapshot, not a post payload
_POSTER_WORDS = {"--poster", "poster"}  # a set, so this module's own literals never look like an argv pair
_INBOX_POST = "inbox post"


def _iter_sources(root: Path):
    for path in sorted(Path(root).rglob("*.py")):
        rel = path.relative_to(root)
        nested_claude_tree = any(a == '.claude' and b == 'worktrees' for a, b in zip(rel.parts, rel.parts[1:]))
        if (any(p in SKIP_DIRS or p == '.worktrees' for p in rel.parts)
                or nested_claude_tree or rel.name.startswith("test_")):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        if "inbox" in text.lower():
            yield rel, tree


def _mentions_poster(node) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in _POSTER_WORDS:
            return True
        if isinstance(n, ast.keyword) and (n.arg == "poster" or n.arg is None):  # poster= or **kwargs forwarding
            return True
        if isinstance(n, ast.Name) and n.id == "poster":
            return True
    return False


def _scopes(tree):
    """(node, enclosing function or module) for every node."""
    def walk(node, scope):
        for child in ast.iter_child_nodes(node):
            inner = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else scope
            yield child, inner
            yield from walk(child, inner)
    yield from walk(tree, tree)


def _is_inbox_post_argv(node) -> bool:
    """[<hermes>, "inbox", "post", ...]: the subcommand follows the program, so it never starts the list."""
    vals = [_const(e) for e in node.elts]
    return any(f"{vals[i]} {vals[i + 1]}" == _INBOX_POST for i in range(1, len(vals) - 1))


def _is_inbox_post_call(node: ast.Call) -> bool:
    f = node.func
    if not (isinstance(f, ast.Attribute) and f.attr == "post"):
        return False
    base = f.value
    name = (base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute)
            else _callee(base) if isinstance(base, ast.Call) else "")
    return "inbox" in name.lower()


def scan_missing(root: Path) -> list[dict]:
    """Return ``{site, kind}`` for every Inbox-posting call under ``root`` that names no poster."""
    out: list[dict] = []
    for rel, tree in _iter_sources(root):
        for n, scope in _scopes(tree):
            kind = None
            if isinstance(n, (ast.List, ast.Tuple)) and _is_inbox_post_argv(n):
                kind = "argv"
            elif isinstance(n, ast.Dict):
                keys = {_const(k) for k in n.keys if k is not None}
                if (_PAYLOAD_KEYS <= keys and keys & _PAYLOAD_BODY and not keys & _SNAPSHOT_KEYS
                        and not keys & _POSTER_WORDS and None not in n.keys):
                    out.append({"site": f"{rel}:{n.lineno}", "kind": "payload"})
                continue
            elif isinstance(n, ast.Call) and _is_inbox_post_call(n):
                if not n.args and not any(k.arg == "key" for k in n.keywords):
                    continue  # not a keyed post (e.g. an HTTP client's .post(url=...))
                kind = "call"
            if kind and not _mentions_poster(scope):
                out.append({"site": f"{rel}:{n.lineno}", "kind": kind})
    return out
