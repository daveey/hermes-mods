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


# The hook fixture above builds a 5-entry line under preview_head, so the trim loop never ran there. Days-old,
# often-revised asks with long titles make each entry ~97 chars, so five of them overflow a real head budget.
_NOW = 2_000_000.0
_DAYS_OLD = 13 * 86400 + 23 * 3600


def _long_pending(n=7):
    pending = [{"requestId": f"{i:08x}-trim-fixture", "title": f"Ask {i}: " + "long scope title words " * 5,
                "revision": 12} for i in range(n)]
    return pending, {r["requestId"]: _NOW - _DAYS_OLD for r in pending}


def _shown_ids(line, pending):
    return [r["requestId"][:8] for r in pending if r["requestId"][:8] in line]


def test_index_trims_from_end_to_fit_budget():
    plugin = load_module("truncation_index_trim", ROOT / "__init__.py")
    pending, created = _long_pending(7)
    untrimmed = plugin._open_asks_index(pending, created, now=_NOW, budget=None)
    assert len(untrimmed) > 250 and len(_shown_ids(untrimmed, pending)) == 5  # the fixture must force trimming
    assert "open 13 d 23 h" in untrimmed
    line = plugin._open_asks_index(pending, created, now=_NOW, budget=250)
    assert len(line) <= 250, line
    shown = _shown_ids(line, pending)
    assert shown == [r["requestId"][:8] for r in pending[:len(shown)]] and 1 <= len(shown) < 5, line
    # +N more counts the capped entries (6th, 7th) and the trimmed ones alike.
    assert line.endswith(f"; +{7 - len(shown)} more]"), line
    assert line.startswith("[Open asks from this chat (7): " + pending[0]["requestId"][:8])


def test_index_budget_boundary_is_inclusive():
    plugin = load_module("truncation_index_boundary", ROOT / "__init__.py")
    pending, created = _long_pending(7)
    two = plugin._open_asks_index(pending, created, now=_NOW, budget=250)
    assert len(_shown_ids(two, pending)) == 2, two
    assert plugin._open_asks_index(pending, created, now=_NOW, budget=len(two)) == two
    one = plugin._open_asks_index(pending, created, now=_NOW, budget=len(two) - 1)
    assert _shown_ids(one, pending) == [pending[0]["requestId"][:8]] and one.endswith("; +6 more]"), one
    assert len(one) <= len(two) - 1


@pytest.mark.parametrize("budget", [40, 10])  # 40 fits the bare line (39 chars); 10 is below even that
def test_index_bare_more_when_no_entry_fits(budget):
    import threading
    plugin = load_module(f"truncation_index_bare_{budget}", ROOT / "__init__.py")
    pending, created = _long_pending(7)
    out = []
    worker = threading.Thread(target=lambda: out.append(
        plugin._open_asks_index(pending, created, now=_NOW, budget=budget)), daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "_open_asks_index looped when no entry fits the budget"
    # The bare form is returned even when it is itself over budget: there is nothing left to drop.
    assert out == ["[Open asks from this chat (7): +7 more]"]
