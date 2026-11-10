"""Lettered menus ("Reply A, B, C or D") never become bare-letter buttons: each option carries its meaning and the
agent's recommendation, looked up in the reply or on the card the reply names."""
import importlib.util
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('lettered_core', ROOT / 'core.py')
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)

OWNER = {'gatewayId': 'test', 'profile': 'default', 'storedSessionId': 'session'}
REF = {'gatewayId': 'test', 'profile': 'default', 'type': 'user_step', 'id': 'us_x'}

REASON = ("1) Reply with A, B, C or D (below).\n"
          "A) Authority: allow a root-owned read-only launchd attribution (e.g. `launchctl print user/600` via one "
          "narrow sudoers/root helper) so launchd-owned services can be exempted. Keeps the exclusion; needs a new "
          "privilege.\n"
          "B) Scope: add a named, disclosed NON-EXCLUDED class \"Apple system services\" (exact exe path + code "
          "identity); receipts disclaim it.\n"
          "C) Scope: bbci interval ends at Worker exit only and receipts disclaim leftover processes. Weakest.\n"
          "D) Leave G2 unmet and keep shared mode off.\n"
          "Recommendation: A if a narrow root helper is acceptable, else B; never by name alone.")
RELAY = ('The A/B/C/D choice I sent in my last message is still the only open ask. Reply A, B, C or D on the '
         '"K2 G2 live prerequisite" card (runners/t_01a1a399994eacc1).')


def board(tmp_path, reason=REASON, choices=None):
    db = tmp_path / 'kanban.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE tasks (id TEXT, human_request TEXT, title TEXT, status TEXT)')
        c.execute('CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT)')
        hr = {'action': 'Reply A, B, C or D', 'choices': choices if choices is not None else [
            {'label': 'A: root launchd attribution', 'value': 'A'},
            {'label': 'B: disclosed Apple-service class', 'value': 'B'},
            {'label': 'C: Worker-exit only for bbci', 'value': 'C'},
            {'label': 'D: leave G2 unmet', 'value': 'D'}]}
        c.execute("INSERT INTO tasks VALUES ('t_01a1a399994eacc1', ?, 'K2', 'blocked')", (json.dumps(hr),))
        c.execute("INSERT INTO task_events (task_id, kind, payload) VALUES ('t_01a1a399994eacc1', 'blocked', ?)",
                  (json.dumps({'reason': reason}),))
    return [('runners', db)]


def step(text=RELAY):
    return dict(id='us_x', text='Reply A, B, C or D on the "K2 G2 live prerequisite" card (runners/t_01a1a399994eacc1)',
                kind='choose', paragraph=text, verbatim_span=text, options=['A', 'B', 'C', 'D'], session='session')


def test_relay_of_card_menu_gets_meanings_and_recommendation(tmp_path):
    out = core.explained_choices(['A', 'B', 'C', 'D'], step(), boards=board(tmp_path))
    assert [c['label'] for c in out] == ['A \u2014 root launchd attribution', 'B \u2014 disclosed Apple-service class',
                                         'C \u2014 Worker-exit only for bbci', 'D \u2014 leave G2 unmet']
    assert out[0]['explain'].startswith('Authority: allow a root-owned read-only launchd attribution')
    assert '`' not in out[0]['explain']
    assert [c.get('recommended') for c in out] == [True, None, None, None]


def test_definitions_in_the_reply_itself(tmp_path):
    reply = ("Which way?\n\n- **A:** keep the nightly build on spot instances, cheapest\n"
             "- **B:** move it to on-demand so it never gets preempted\n\nI recommend B.")
    out = core.explained_choices(['A', 'B'], step(reply), boards=[])
    assert [c['label'] for c in out] == ['A \u2014 keep the nightly build on spot instances, cheapest',
                                         'B \u2014 move it to on-demand so it never gets preempted']
    assert [c.get('recommended') for c in out] == [None, True]


def test_undefined_codes_are_dropped_not_shown_bare(tmp_path):
    out = core.explained_choices(['A', 'B'], step('Reply A or B on the card.'), boards=[])
    assert out == []


def test_word_labels_pass_through_unchanged():
    out = core.explained_choices(['Now', 'Later'], step('Run it now or later?'), boards=[])
    assert out == [{'id': 'answer-0', 'label': 'Now', 'effect': 'answer'},
                   {'id': 'answer-1', 'label': 'Later', 'effect': 'answer'}]


def test_legacy_request_never_publishes_a_bare_letter(tmp_path, monkeypatch):
    b = board(tmp_path)
    monkeypatch.setattr(core, 'kanban_boards', lambda root=None: b)
    r = core.legacy_request(OWNER, step(), REF)
    labels = [c['label'] for c in r['choices']]
    assert not any(core.option_code(label) for label in labels), labels
    assert labels[-1] == 'Answer in your own words'
    assert sum(1 for c in r['choices'] if c.get('recommended')) == 1
    core.ActionStore(tmp_path / 'a.sqlite3').create(OWNER, r)  # passes the v2 validator
