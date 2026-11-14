"""A bare letter typed in chat means the decision panel's lettered option, not a plan part.

Incident agent-d61f5b16f5: a blocked card asked 'Go: A+B+C' / 'A only (cadence)' / 'No, keep instant'.
The Desktop panel badges those options A, B, C (desktop-plugins/user-steps/plugin.js `letter(index)`).
David typed "a" (panel option A = Go: A+B+C); the agent only saw choice ids card-0.. with no letters, read
"a" as plan part A, and narrowed the scope to "A only". The per-turn hook must map the letter for it.
"""

import json

from test_kanban_mirror import fixture
from test_withdrawal import ROOT, OWNER, load_module

INCIDENT_CHOICES = [("Go: A+B+C", "go"), ("A only (cadence)", "a-only"), ("No, keep instant", "no")]


def _incident_mirror(tmp_path, recommended=None):
    mirror, store, path, hr = fixture(tmp_path)
    import sqlite3

    options = [dict(label=label, value=value) for label, value in INCIDENT_CHOICES]
    if recommended is not None:
        options[recommended]["label"] += " (recommended)"
    hr = dict(hr, choices=options)
    c = sqlite3.connect(path)
    c.execute("UPDATE tasks SET human_request=? WHERE id='t_test'", (json.dumps(hr),))
    c.commit()
    c.close()
    mirror.reconcile(OWNER)
    return mirror, store


def _plugin(name, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    return load_module(name, ROOT / "__init__.py")


def test_bare_letter_maps_to_panel_option_not_plan_part(tmp_path, monkeypatch):
    plugin = _plugin("panel_letter_pure", tmp_path, monkeypatch)
    mirror, store = _incident_mirror(tmp_path)
    presented = [mirror.present(r) for r in store.requests(OWNER)]
    hint = plugin._panel_letter_hint("a", presented)
    assert 'A = "Go: A+B+C"' in hint
    assert 'B = "A only (cadence)"' in hint
    assert "plan" in hint.lower()  # names the namespace collision explicitly


def test_recommended_option_is_letter_a(tmp_path, monkeypatch):
    plugin = _plugin("panel_letter_rec", tmp_path, monkeypatch)
    mirror, store = _incident_mirror(tmp_path, recommended=2)
    presented = [mirror.present(r) for r in store.requests(OWNER)]
    hint = plugin._panel_letter_hint("A", presented)
    assert 'A = "No, keep instant"' in hint  # the panel lists the recommended option first
    assert 'B = "Go: A+B+C"' in hint


def test_letter_with_trailing_words_and_non_letter_messages(tmp_path, monkeypatch):
    plugin = _plugin("panel_letter_forms", tmp_path, monkeypatch)
    mirror, store = _incident_mirror(tmp_path)
    presented = [mirror.present(r) for r in store.requests(OWNER)]
    assert 'B = "A only (cadence)"' in plugin._panel_letter_hint("b - skip that, another session has it", presented)
    assert 'C = "No, keep instant"' in plugin._panel_letter_hint("(c)", presented)
    assert 'A = "Go: A+B+C"' in plugin._panel_letter_hint("a.", presented)
    assert 'A = "Go: A+B+C"' in plugin._panel_letter_hint("A: go", presented)
    assert 'B = "A only (cadence)"' in plugin._panel_letter_hint("b, please", presented)
    assert 'B = "A only (cadence)"' in plugin._panel_letter_hint("option b", presented)
    assert 'A = "Go: A+B+C"' in plugin._panel_letter_hint("(a) all of it", presented)
    assert 'C = "No, keep instant"' in plugin._panel_letter_hint("c \u2014 not now", presented)
    for text in ("a new idea: batch it", "go", "do everything", "", "z"):
        assert plugin._panel_letter_hint(text, presented) == "", text
    # Abbreviations and hyphenated words are English, not option letters (PR #188 review): with A-E shown,
    # "e.g. ..." would map to Other and "a.k.a."/"A-OK" to the recommended option, the incident in reverse.
    for text in ("e.g. do the first one", "i.e. that one", "e-mail him", "a.k.a. the plan", "A-OK",
                 "x-ray it", "t-shirt", "u.s. only", "P.S. thanks", "a-ha"):
        assert plugin._panel_letter_hint(text, presented) == "", text
    # Unlettered (non-card) requests never produce a mapping.
    assert plugin._panel_letter_hint("a", [dict(r, presentation=None) for r in presented]) == ""


def test_pre_llm_call_puts_letter_mapping_in_turn_context(tmp_path, monkeypatch):
    plugin = _plugin("panel_letter_hook", tmp_path, monkeypatch)
    mirror, store = _incident_mirror(tmp_path)
    monkeypatch.setattr(plugin.core, "home", lambda: tmp_path)
    monkeypatch.setattr(plugin.core, "kanban_root", lambda: tmp_path)
    monkeypatch.setattr(plugin.core, "server_owner", lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(plugin.core, "ActionStore", lambda *a, **kw: store)
    monkeypatch.setattr(plugin.chat, "reconcile", lambda *a, **kw: None)
    monkeypatch.setattr(plugin, "_presenter", lambda st: mirror, raising=False)
    result = plugin.pre_llm_call(session_id="session-a", platform="desktop", user_message="a")
    assert result and 'A = "Go: A+B+C"' in result["context"]
    # Placed first, so a head/tail spill of the long request JSON can never hide it.
    assert result["context"].startswith("[Decision panel letters")
    quiet = plugin.pre_llm_call(session_id="session-a", platform="desktop", user_message="go")
    assert "Decision panel letters" not in (quiet or {}).get("context", "")


def test_pre_llm_call_builds_the_real_presenter(tmp_path, monkeypatch):
    """No presenter stub: the hook's own Mirror must find the card projection the queue presents."""
    plugin = _plugin("panel_letter_real", tmp_path, monkeypatch)
    _, store = _incident_mirror(tmp_path)
    monkeypatch.setattr(plugin.core, "home", lambda: tmp_path)
    monkeypatch.setattr(plugin.core, "kanban_root", lambda: tmp_path)
    monkeypatch.setattr(plugin.core, "server_owner", lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(plugin.core, "ActionStore", lambda *a, **kw: store)
    monkeypatch.setattr(plugin.chat, "reconcile", lambda *a, **kw: None)
    result = plugin.pre_llm_call(session_id="session-a", platform="desktop", user_message="a")
    assert result and result["context"].startswith('[Decision panel letters: the user\'s reply starts with "a"')
    assert 'A = "Go: A+B+C", B = "A only (cadence)", C = "No, keep instant", D = "Skip"' in result["context"]


def test_python_letter_order_still_matches_the_desktop_panel():
    """_panel_rows mirrors plugin.js; if the panel's lettering or order changes, update both together."""
    js = (ROOT.parents[1] / "desktop-plugins" / "user-steps" / "plugin.js").read_text()
    assert "const letter = i => String.fromCharCode(65 + i)" in js
    assert "const ordered = recommended ? [recommended, ...shown.filter(c => c !== recommended)] : shown" in js
    assert "questionRow('skip', ordered.length, 'Skip'" in js
    assert "questionRow('answer-text', ordered.length + 1, 'Other (type your answer)'" in js
    assert "presentation?.kind === 'kanban-question'" in js
