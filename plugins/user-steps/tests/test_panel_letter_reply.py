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


def test_only_exact_legacy_placeholder_is_hidden_from_panel_letters(tmp_path, monkeypatch):
    plugin = _plugin("panel_letter_placeholder", tmp_path, monkeypatch)
    request = {
        "requestId": "panel-placeholder",
        "title": "Choose the next action",
        "presentation": {
            "kind": "kanban-question", "question": "What next?", "details": "",
            "recommendedChoiceId": "proceed", "other": True,
        },
        "choices": [
            {"id": "request-context", "effect": "revise", "label": "Revise the scope"},
            {"id": "proceed", "effect": "approve", "label": "Proceed (recommended)"},
        ],
    }
    # Matching the legacy id/effect alone must not hide a legitimate choice.
    assert plugin._panel_rows(request) == [
        "Proceed", "Revise the scope", "Skip", "Other (type your answer)",
    ]
    assert ('A = "Proceed", B = "Revise the scope", C = "Skip", '
            'D = "Other (type your answer)"') in plugin._panel_letter_hint("b", [request])
    assert plugin._panel_letter_hint("d", [request])

    # Change only the label to the exact retired placeholder: the following letters shift.
    placeholder = dict(request, choices=[
        dict(request["choices"][0], label="Request complete action"), request["choices"][1],
    ])
    assert plugin._panel_rows(placeholder) == ["Proceed", "Skip", "Other (type your answer)"]
    hint = plugin._panel_letter_hint("b", [placeholder])
    assert 'A = "Proceed", B = "Skip", C = "Other (type your answer)"' in hint
    assert "Request complete action" not in hint
    assert plugin._panel_letter_hint("d", [placeholder]) == ""


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


def _question(**overrides):
    request = {
        "requestId": "question-one", "title": "Choose a route",
        "presentation": {"kind": "kanban-question", "question": "Which route?",
                         "details": "Choose one", "other": True},
        "choices": [{"id": "first", "label": "First route"},
                    {"id": "second", "label": "Second route"}],
    }
    request.update(overrides)
    return request


def test_other_is_lettered_only_when_offered(tmp_path, monkeypatch):
    plugin = _plugin("panel_letter_other", tmp_path, monkeypatch)
    request = _question()
    assert plugin._panel_rows(request) == ["First route", "Second route", "Skip", "Other (type your answer)"]
    assert 'D = "Other (type your answer)"' in plugin._panel_letter_hint("d", [request])
    request["presentation"]["other"] = False
    assert plugin._panel_letter_hint("d", [request]) == ""


def test_only_legacy_request_context_placeholder_is_filtered(tmp_path, monkeypatch):
    plugin = _plugin("panel_letter_legacy", tmp_path, monkeypatch)
    request = _question(choices=[
        {"id": "request-context", "effect": "revise", "label": "Request complete action"},
        {"id": "first", "label": "First route"},
    ])
    assert plugin._panel_rows(request) == ["First route", "Skip", "Other (type your answer)"]
    assert 'A = "First route"' in plugin._panel_letter_hint("a", [request])
    # PR190 preserves genuine revise choices; matching the id/effect alone is insufficient.
    request["choices"][0]["label"] = "Change the route"
    assert plugin._panel_rows(request) == ["Change the route", "First route", "Skip", "Other (type your answer)"]
    request["choices"][0].update(label="Request complete action", effect="select")
    assert plugin._panel_rows(request)[0] == "Request complete action"
    request["choices"][0].update(id="real-choice", effect="revise")
    assert plugin._panel_rows(request)[0] == "Request complete action"


def test_non_question_presentation_never_assigns_letters(tmp_path, monkeypatch):
    plugin = _plugin("panel_letter_kind", tmp_path, monkeypatch)
    request = _question()
    assert plugin._panel_letter_hint("a", [request])
    request["presentation"]["kind"] = "approval"
    assert plugin._panel_rows(request) == []
    assert plugin._panel_letter_hint("a", [request]) == ""


def test_multiple_matching_asks_require_disambiguation(tmp_path, monkeypatch):
    plugin = _plugin("panel_letter_multiple", tmp_path, monkeypatch)
    first = _question()
    second = _question(requestId="another-request", title="Choose a time")
    hint = plugin._panel_letter_hint("a", [first, second])
    assert '2 open asks have a "A": ask which one before acting.' in hint
    assert '"Choose a route" (requestId question)' in hint
    assert '"Choose a time" (requestId another-)' in hint
    assert "ask which one before acting" not in plugin._panel_letter_hint("a", [first])


def test_choice_recommended_flag_orders_without_presentation_id(tmp_path, monkeypatch):
    plugin = _plugin("panel_letter_flag", tmp_path, monkeypatch)
    request = _question()
    request["choices"][1]["recommended"] = True
    assert "recommendedChoiceId" not in request["presentation"]
    assert plugin._panel_rows(request) == ["Second route", "First route", "Skip", "Other (type your answer)"]
    assert 'A = "Second route", B = "First route"' in plugin._panel_letter_hint("a", [request])


def test_pre_llm_call_contains_mapping_failure_and_keeps_decisions(tmp_path, monkeypatch, caplog):
    plugin = _plugin("panel_letter_failure", tmp_path, monkeypatch)
    _, store = _incident_mirror(tmp_path)
    monkeypatch.setattr(plugin.core, "home", lambda: tmp_path)
    monkeypatch.setattr(plugin.core, "kanban_root", lambda: tmp_path)
    monkeypatch.setattr(plugin.core, "server_owner", lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(plugin.core, "ActionStore", lambda *a, **kw: store)
    monkeypatch.setattr(plugin.chat, "reconcile", lambda *a, **kw: None)
    calls = []

    def broken_mapping(message, requests):
        calls.append((message, requests))
        raise ValueError("synthetic mapping failure")

    monkeypatch.setattr(plugin, "_panel_letter_hint", broken_mapping)
    result = plugin.pre_llm_call(session_id="session-a", platform="desktop", user_message="a")
    assert len(calls) == 1 and calls[0][0] == "a" and calls[0][1]
    assert result and "Canonical requests awaiting a user decision" in result["context"]
    assert store.requests(OWNER)[0]["requestId"] in result["context"]
    assert "Decision panel letters" not in result["context"]
    assert "user-steps panel letter mapping failed" in caplog.text
    assert "synthetic mapping failure" in caplog.text
    assert "user-steps pre_llm_call failed" not in caplog.text


def test_python_letter_order_still_matches_the_desktop_panel():
    """_panel_rows mirrors plugin.js; if the panel's lettering or order changes, update both together."""
    js = (ROOT.parents[1] / "desktop-plugins" / "user-steps" / "plugin.js").read_text()
    assert "const letters = panelLetters(ordered.map(c => c.label), extraRows)" in js
    assert "const extraRows = 1 + (presentation?.other === true ? 1 : 0)" in js
    assert "const OWN_LETTER = /^\\s*(?:option\\s+)?\\(?([A-Za-z])(?:\\)|:|\\.(?=\\s)|\\s+[-\\u2013\\u2014](?=\\s))\\s*/i" in js
    assert "const ordered = recommended ? [recommended, ...shown.filter(c => c !== recommended)] : shown" in js
    assert "questionRow('skip', ordered.length, 'Skip'" in js
    assert "questionRow('answer-text', ordered.length + 1, 'Other (type your answer)'" in js
    assert "presentation?.kind === 'kanban-question'" in js


def test_own_option_letters_survive_recommended_first_order(tmp_path, monkeypatch):
    """A card whose choices are labelled "C: …" / "A: …" keeps C and A, recommended first; Skip/Other follow."""
    plugin = _plugin("panel_letter_own", tmp_path, monkeypatch)
    request = {
        "requestId": "33333333-3333-4333-8333-333333333333", "title": "Pick a route",
        "choices": [{"id": "card-0", "label": "C: test Slack on the live version (recommended)", "effect": "answer"},
                    {"id": "card-1", "label": "A: fund the full proof", "effect": "answer"}],
        "presentation": {"kind": "kanban-question", "question": "Pick", "details": "d",
                         "recommendedChoiceId": "card-0", "other": True},
    }
    rows = plugin._panel_rows(request)
    assert plugin._panel_letters(rows, 2) == ["C", "A", "D", "E"]
    hint = plugin._panel_letter_hint("c", [request])
    assert 'C = "C: test Slack on the live version"' in hint and 'A = "A: fund the full proof"' in hint
    assert 'D = "Skip"' in hint
    assert plugin._panel_letter_hint("b", [request]) == ""


def test_mixed_or_duplicate_own_letters_fall_back_to_position(tmp_path, monkeypatch):
    plugin = _plugin("panel_letter_mixed", tmp_path, monkeypatch)
    assert plugin._panel_letters(["A only (cadence)", "Go: A+B+C", "Skip"], 1) == ["A", "B", "C"]
    assert plugin._panel_letters(["A: one", "two", "Skip"], 1) == ["A", "B", "C"]
    assert plugin._panel_letters(["A: one", "A) two", "Skip"], 1) == ["A", "B", "C"]
