"""Retired 'Request complete action' fallback: every kind re-renders, idle sessions too."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = load('backfill_core', ROOT / 'core.py')
chat = load('backfill_chat', ROOT / 'chat.py')
OWNER = {'gatewayId': 'install:test', 'profile': 'default', 'storedSessionId': 's1'}
FALLBACK = 'Request complete action'

# Live repro (Diplomacy, hub t_9fd018d4): model options are paraphrases, not source text.
DIPLO_SPAN = ("Should it make a clearly labeled animation rebuilt from the replay's recorded moves, "
              "or should web Diplomacy be left out of the clip set?")
DIPLO = dict(id='us_diplo', session='s1', kind='choose', status='open',
             text='Decide whether web Diplomacy gets a labeled animation rebuilt from recorded moves, '
                  'or is left out of the clip set',
             verbatim_span=DIPLO_SPAN,
             options=['Make a labeled animation from recorded moves', 'Leave web Diplomacy out of the clip set'],
             paragraph='One decision is still open. I read the web Diplomacy task on the hub (t_9fd018d4) just '
                       'now, and it\'s still blocked waiting on you. ' + DIPLO_SPAN)
DO = dict(id='us_do', session='s1', kind='do', status='open', options=[],
          text='Record your voice in the master bedroom and say "ready"',
          verbatim_span='Record your voice in the master bedroom', paragraph='Record your voice in the master bedroom, then say "ready".')
APPROVE = dict(id='us_ap', session='s1', kind='approve', status='open', options=[],
               text='Consent to a Desktop SSH disconnect', verbatim_span='that specific disruption still needs your consent.',
               paragraph='If the bootstrap disconnects a Desktop session, that specific disruption still needs your consent.')


def legacy_row(st, step, owner=OWNER):
    """Exactly what the pre-fix migrate_step stored."""
    ref = dict(gatewayId=owner['gatewayId'], profile=owner['profile'], type='user_step', id=step['id'])
    kind = {'approve': 'approval', 'choose': 'choice', 'do': 'external_step'}.get(step['kind'], 'input')
    return st.create(owner, {
        'schemaVersion': 2, 'owner': owner, 'requestId': core.uuid.uuid4().__str__(), 'revision': 1, 'kind': kind,
        'title': step['text'], 'scope': {'markdown': step['paragraph'], 'complete': False, 'sourceRefs': [ref]},
        'limits': [], 'exclusions': [], 'outcomes': {'revise': 'Request complete action; keep work blocked.'},
        'choices': [dict(c) for c in core.LEGACY_FALLBACK], 'aliases': [ref], 'state': 'needs_context'})


@pytest.fixture
def env(tmp_path):
    st = core.ActionStore(tmp_path / 'actions.sqlite3')
    ledger = core.Ledger(tmp_path / 'ledger', tmp_path / 'state.db')
    with ledger.edit('s1') as d:
        d['steps'].extend([dict(DIPLO), dict(DO), dict(APPROVE)])
    return st, ledger


def labels(r):
    return [c['label'] for c in r['choices']]


def test_diplomacy_literal_options_untruncated():
    assert core.literal_options(DIPLO) == [
        "Make a clearly labeled animation rebuilt from the replay's recorded moves",
        'Should web Diplomacy be left out of the clip set']
    # each label is verbatim source text (first-letter case aside), never a paraphrase
    for label in core.literal_options(DIPLO):
        assert label[1:] in DIPLO['paragraph']


# Live repro desktop-1321064734 (MacBook 2026-10-27): the model's options are a paraphrase ("Open a PR") plus a
# literal "Something else"; the panel showed only "something else" and lost the real choice.
PR_SPAN = 'Do you mean open a pull request implementing these strategy changes, or something else?'
PR = dict(id='us_4e904be48c', session='s1', kind='choose', status='open', text='Choose: open a PR or something else',
          verbatim_span=PR_SPAN, options=['Open a PR', 'Something else'],
          paragraph='I can do that. ' + PR_SPAN)


def test_reported_pr_question_keeps_its_real_option(env):
    st, _ = env
    assert core.literal_options(PR) == ['Open a pull request implementing these strategy changes']
    r = st.migrate_step(OWNER, dict(PR))
    assert r['kind'] == 'choice' and r['state'] == 'open' and r['scope']['complete'] is True
    # one button per real option; "something else" is the free-text answer, not a dead button
    assert labels(r) == ['Open a pull request implementing these strategy changes', 'Answer in your own words']
    assert all(c['effect'] == 'answer' for c in r['choices'])


def test_other_tail_alone_is_never_a_button():
    step = dict(PR, options=['Something else', 'Neither'], verbatim_span='Should I add something else?',
                paragraph='Should I add something else?')
    assert core.literal_options(step) == []


def test_validate_keeps_long_options():
    opt = "make a clearly labeled animation rebuilt from the replay's recorded moves"
    assert len(opt) > 60
    out = core.validate([{'text': 'Decide', 'kind': 'choose', 'verbatim_span': DIPLO_SPAN,
                          'options': [opt, 'web Diplomacy be left out of the clip set']}], DIPLO['paragraph'], [])
    assert out and out[0]['options'][0] == opt


@pytest.mark.parametrize('span,expected', [
    ('Do you mean open a pull request implementing these strategy changes, or something else?',
     ['Open a pull request implementing these strategy changes']),
    ('Which one do you want?', []),                       # no literal alternatives
    ('Should I use A or B or C?', []),                    # ambiguous split: free text only
    # live row 37aee9d1: two open wh-questions are not pickable options; markup is framing
    ('**Ask:** which model ID should the Fabric study use for Gemini Argon, or which model should it use instead?', []),
    ('**Ask:** should it keep the 320 GiB volume, or shut it down?', ['Keep the 320 GiB volume', 'Shut it down']),
])
def test_question_alternatives(span, expected):
    assert core._question_alternatives(span, span) == expected


def test_backfill_rerenders_every_kind_and_never_shows_fallback(env):
    st, ledger = env
    rows = [legacy_row(st, s) for s in (DIPLO, DO, APPROVE)]
    done = st.backfill_legacy(ledger, gateway='install:test')
    assert len(done) == 3
    after = {r['requestId']: r for r in st.requests(OWNER)}
    diplo, do, ap = (after[r['requestId']] for r in rows)
    for r in (diplo, do, ap):
        assert r['revision'] == 2
        assert FALLBACK not in json.dumps(r)
    assert diplo['state'] == 'open' and labels(diplo) == core.literal_options(DIPLO) + ['Answer in your own words']
    assert do['state'] == 'open' and labels(do) == ['Done', "Can't do it / reply in your own words"]
    # approvals stay agent-incomplete: no approve choice, no permission, no chat marker
    assert ap['state'] == 'needs_context' and ap['scope']['complete'] is False
    assert {c['effect'] for c in ap['choices']} == {'revise'}
    with st._connect() as c:
        marked = {row[0] for row in c.execute('SELECT request FROM clarification_upgrades')}
        assert c.execute('SELECT count(*) FROM operations').fetchone()[0] == 0
    assert rows[2]['requestId'] not in marked and rows[0]['requestId'] in marked
    # idempotent
    assert st.backfill_legacy(ledger, gateway='install:test') == []


def test_backfill_answers_grant_nothing(env):
    st, ledger = env
    diplo, do = legacy_row(st, DIPLO), legacy_row(st, DO)
    st.backfill_legacy(ledger)
    for rid, choice, text in ((diplo['requestId'], 'answer-0', core.literal_options(DIPLO)[0]),
                              (do['requestId'], 'answer-done', 'Done')):
        op = st.decide(OWNER, dict(requestId=rid, expectedRevision=2, choiceId=choice,
                                   revisionText=text, idempotencyKey=rid))['operation']
        assert op['intent']['executionAuthorized'] is False and op['intent']['permissionGrants'] == []
    with pytest.raises(core.ActionError):
        st.decide(OWNER, dict(requestId=do['requestId'], expectedRevision=2, choiceId='answer-done',
                              revisionText='Done and approve everything', idempotencyKey='x'))


def test_backfill_skips_foreign_gateway_and_mismatched_session(env, tmp_path):
    st, ledger = env
    other = dict(OWNER, gatewayId='install:other')
    foreign = legacy_row(st, DIPLO, other)
    assert st.backfill_legacy(ledger, gateway='install:test') == []
    assert st.get(other, foreign['requestId'])['revision'] == 1
    # a ledger step from another session never binds
    with ledger.edit('s1') as d:
        for s in d['steps']:
            s['session'] = 's2'
    st2 = core.ActionStore(tmp_path / 'b.sqlite3')
    row = legacy_row(st2, DIPLO)
    assert st2.backfill_legacy(ledger) == []
    assert st2.get(OWNER, row['requestId'])['revision'] == 1


def test_backfill_never_touches_resolved_or_producer_rows(env):
    st, ledger = env
    row = legacy_row(st, DIPLO)
    with st._connect() as c:
        c.execute("UPDATE requests SET state='superseded' WHERE id=?", (row['requestId'],))
    assert st.backfill_legacy(ledger) == []
    assert st.get(OWNER, row['requestId'])['revision'] == 1


def test_new_steps_get_real_shapes(env):
    st, _ = env
    for step in (DIPLO, DO, APPROVE):
        r = st.migrate_step(OWNER, dict(step, id=step['id'] + '_new'))
        assert r['revision'] == 1 and FALLBACK not in json.dumps(r)


def test_upgraded_diplomacy_retires_from_chat_only_with_evidence(env, tmp_path):
    st, ledger = env
    row = legacy_row(st, DIPLO)
    st.backfill_legacy(ledger)
    db = tmp_path / 'state.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,'
                  'active INTEGER,display_kind TEXT,tool_calls TEXT)')
        c.executemany('INSERT INTO messages VALUES (?,?,?,?,1,NULL,NULL)', [
            (1, 's1', 'assistant', core.directive(['us_diplo'], 's1', DIPLO['paragraph'])),
            (2, 's1', 'user', 'ok')])
    judge = lambda _s, u: json.dumps({'outcome': 'answered', 'quote': json.loads(u)['answer']})
    assert chat.reconcile(core, st, OWNER, judge, db) == []          # vague assent stays open
    with sqlite3.connect(db) as c:
        c.execute("UPDATE messages SET content='leave it out of the clip set' WHERE id=2")
    out = chat.reconcile(core, st, OWNER, judge, db)
    assert out[0]['request']['requestId'] == row['requestId'] and out[0]['request']['state'] == 'superseded'
    assert out[0]['chatEvidence']['executionAuthorized'] is False
