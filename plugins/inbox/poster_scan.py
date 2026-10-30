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
