"""Detection guard: every in-repo caller of ``Inbox().post`` must make a post the store accepts.

chat-closeout's watchdog called ``Inbox().post(title, body, key=..., poster="chat-closeout")`` with no
accept_prompt; the store refused it on every run ("accept_prompt is required") and the failure only showed as a
WARNING in errors.log. Its own tests swapped in a fake post function, so the real call never ran in CI.

This test walks hermes-mods for ``<...>.Inbox(...).post(...)`` calls (and ``x = <...>.Inbox(...)`` then
``x.post(...)`` in the same function), and replays each against a temp store with the call's real shape: the
literal ``poster=``/``type=`` it passes, and an accept_prompt only if the caller supplies one. Pass-through
callers (tool/CLI/REST/forwarders that hand a caller's accept_prompt through) supply one, so they replay clean.
"""
import ast
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
REPO = HERE.parents[1]


def _is_inbox_ctor(node) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (isinstance(f, ast.Name) and f.id == "Inbox") or (isinstance(f, ast.Attribute) and f.attr == "Inbox")


def _literal_kw(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value.value if isinstance(kw.value, ast.Constant) else ...
    return None


def find_callers(root: Path) -> list[dict]:
    """Every Inbox post call site under ``root`` (tests and caches excluded), with the shape it posts."""
    out = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(p in ("tests", "__pycache__", ".git", "node_modules") for p in rel.parts) or rel.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))]
        for scope in [tree, *funcs]:
            bound = {t.id for n in ast.walk(scope) if isinstance(n, ast.Assign) and _is_inbox_ctor(n.value)
                     for t in n.targets if isinstance(t, ast.Name)}
            for n in ast.walk(scope):
                if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "post"):
                    continue
                recv = n.func.value
                if not (_is_inbox_ctor(recv) or (isinstance(recv, ast.Name) and recv.id in bound)):
                    continue
                has_prompt = (len(n.args) >= 3 or any(kw.arg in ("accept_prompt", None) for kw in n.keywords))
                site = f"{rel}:{n.lineno}"
                if any(c["site"] == site for c in out):
                    continue
                out.append(dict(site=site, has_prompt=has_prompt, poster=_literal_kw(n, "poster"),
                                has_ask=any(kw.arg in ("ask", None) for kw in n.keywords),
                                type=_literal_kw(n, "type")))
    return out


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("_inbox_store_inrepo_test", HERE / "store.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "kanban_task_creator", lambda **kw: "t_test")  # no real board
    return mod


def _replay(store, caller):
    """Replay a call site's real shape. Pass-through sites (poster/ask handed down from the caller) get the
    shipped 'cli' poster and a valid ask; literal posters are replayed verbatim and must be registered."""
    from ask_fixtures import ASK
    kw = {}
    kw["poster"] = caller["poster"] if isinstance(caller["poster"], str) else "cli"
    if isinstance(caller["type"], str):
        kw["type"] = caller["type"]
    if caller.get("has_ask"):
        kw["ask"] = ASK
    store.Inbox().post("replay", "replay body", "replay instruction" if caller["has_prompt"] else "",
                       key=f"replay:{caller['site']}", **kw)


def test_scanner_finds_the_live_callers():
    sites = {c["site"].split(":")[0] for c in find_callers(REPO)}
    assert "plugins/inbox/__init__.py" in sites  # the scanner must see real callers, or the guard is vacuous


def test_every_inrepo_inbox_post_caller_is_accepted(store):
    bad = []
    for caller in find_callers(REPO):
        try:
            _replay(store, caller)
        except Exception as e:  # noqa: BLE001 - every refusal is a finding
            bad.append(f"{caller['site']}: {type(e).__name__}: {e}")
    assert not bad, "in-repo Inbox posts the store refuses at runtime:\n" + "\n".join(bad)


def test_guard_catches_the_chat_closeout_shape(store, tmp_path):
    """The exact call chat-closeout made before the fix is caught (red without the fix)."""
    src = tmp_path / "mods" / "plugins" / "x"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text(
        "def post_inbox(title, body, key):\n"
        "    mod = load()\n"
        "    mod.Inbox().post(title, body, key=key, poster='chat-closeout')\n")
    [caller] = find_callers(tmp_path / "mods")
    assert caller["has_prompt"] is False and caller["poster"] == "chat-closeout"
    with pytest.raises((ValueError, store.AgentFixablePost)):
        _replay(store, caller)


def test_chat_closeout_is_refused_as_agent_fixable(store):
    """Even with a 'Do it', a chat-closeout post is agent-fixable board state: refused with the Kanban hint."""
    with pytest.raises(store.AgentFixablePost, match="Kanban"):
        store.Inbox().post("stuck chat", "b", "open the chat", key="k", poster="chat-closeout")
