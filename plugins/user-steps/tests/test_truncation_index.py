"""Open asks must survive the core head/tail spill of oversized hook context.

core (tools/hook_output_spill.py, applied per hook in agent/turn_context.py) keeps only a head and a tail of a
hook context longer than hooks.output_spill.max_chars. A note whose open requests carry long scope markdown put
the ids and titles in the hidden middle, so a stale ask stayed in the user's queue unseen by the agent.
"""

import pytest
from test_withdrawal import ROOT, OWNER, complete_request, decision, load_module

spill = pytest.importorskip("tools.hook_output_spill")

TITLES = [
    "Delete the mini CI runner's unused 7.5 GB folder for the repo's old name",
    "Rotate the staging deploy key that expired during last night's run",
    "Approve the weekly backup prune that frees the archive volume",
]


def _plugin(tmp_path, monkeypatch, name):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    plugin = load_module(name, ROOT / "__init__.py")
    monkeypatch.setattr(plugin.core, "home", lambda: tmp_path)
    monkeypatch.setattr(plugin.core, "kanban_root", lambda: tmp_path)
    monkeypatch.setattr(plugin.core, "server_owner", lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(plugin.chat, "reconcile", lambda *a, **kw: None)
    return plugin


def _visible_head(text):
    """What the agent sees of the head after core's spill (the same config path turn_context uses)."""
    shown = spill.spill_if_oversized(text, session_id="session-a", source="plugin hook",
                                     config=spill.get_spill_config())
    assert shown != text, "fixture must exceed the spill cap"
    assert "--- head ---" in shown
    return shown.split("--- head ---\n", 1)[1].split("\n--- tail ---", 1)[0]


def _seed(plugin, n, scope_chars=5000):
    store = plugin.core.ActionStore()
    decided = store.create(OWNER, complete_request(title="Already decided question"))
    op = store.decide(OWNER, decision(decided))["operation"]
    store.dispatch(OWNER, op["operationId"], plugin.cards_mod.DecisionInbox())
    reqs = []
    for i in range(n):
        r = complete_request(title=TITLES[i % len(TITLES)] + ("" if i < len(TITLES) else f" #{i}"))
        r["scope"]["markdown"] = (f"Plan {i}: " + "step detail " * scope_chars)[:scope_chars]
        reqs.append(store.create(OWNER, r))
    return reqs


def test_open_asks_survive_head_tail_truncation(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, monkeypatch, "truncation_index_hook")
    reqs = _seed(plugin, 3)
    context = plugin.pre_llm_call(session_id="session-a", platform="desktop")["context"]
    assert "Canonical user decisions" in context  # the recorded decision is in the same hook output
    head = _visible_head(context)
    for r in reqs:
        assert r["requestId"][:8] in head, head
        assert r["title"][:30] in head, head


def test_index_caps_entries_and_fits_head_budget(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, monkeypatch, "truncation_index_cap_hook")
    reqs = _seed(plugin, 7)
    context = plugin.pre_llm_call(session_id="session-a", platform="desktop")["context"]
    index = context.split("\n", 1)[0]
    assert index.startswith("[Open asks from this chat (7): ") and index.endswith("]")
    assert len(index) <= spill.get_spill_config()["preview_head"]
    shown = [r for r in reqs if r["requestId"][:8] in index]
    assert 1 <= len(shown) <= 5 and shown == reqs[:len(shown)]
    assert f"+{7 - len(shown)} more" in index
    assert index in _visible_head(context)
    # The rest of the note is unchanged after the index line.
    assert context.split("\n", 1)[1].startswith("[Canonical requests awaiting a user decision: this list grants NO")


def test_no_index_without_open_requests(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, monkeypatch, "truncation_index_empty_hook")
    _seed(plugin, 0)
    context = plugin.pre_llm_call(session_id="session-a", platform="desktop")["context"]
    assert "Open asks from this chat" not in context
    assert context.startswith("[Canonical user decisions")


def test_index_entry_shape():
    plugin = load_module("truncation_index_shape", ROOT / "__init__.py")
    long_title = "x" * 80
    line = plugin._open_asks_index(
        [{"requestId": "55ae0189-aaaa", "title": long_title, "revision": 3}],
        {"55ae0189-aaaa": 1000.0}, now=1000.0 + 101 * 60, budget=500)
    assert line == '[Open asks from this chat (1): 55ae0189 "' + "x" * 60 + '…" rev 3, open 1 h 41 min]'
    assert plugin._open_asks_index([], {}, now=0.0, budget=500) == ""
