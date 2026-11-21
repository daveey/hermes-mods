"""Worker-session questions are mirrored, never answered in the worker store."""
import json
import re
import sqlite3
import uuid

import pytest
from test_kanban_mirror import fixture
from test_withdrawal import OWNER, complete_request, core


def cited_fixture(tmp_path, *, own_choices=False):
    mirror, store, path, hr = fixture(tmp_path)
    originals = []
    for title in ('Who may read the raw channel?', 'Which first check family?'):
        worker = dict(OWNER, storedSessionId='local-worker')
        r = complete_request(title=title)
        r.update(owner=worker, kind='choice', outcomes={'answer': 'Record answer'},
                 choices=[dict(id='small', label='Small', effect='answer', explain='Bounded', recommended=True),
                          dict(id='large', label='Large', effect='answer')])
        originals.append(store.create(worker, r))
    if not own_choices:
        hr.pop('choices')
    hr.update(action='Choose each separate question', scope='Choose separately',
              evidence=originals[0]['requestId'])
    with sqlite3.connect(path) as c:
        c.execute('ALTER TABLE task_events ADD COLUMN created_at INTEGER')
        c.execute('UPDATE tasks SET human_request=?', (json.dumps(hr),))
        c.execute('UPDATE task_events SET payload=?',
                  (json.dumps({'reason': originals[1]['requestId'] + ' ' + originals[0]['requestId']}),))
    return mirror, store, path, hr, originals


def answer(store, request):
    return store.decide(OWNER, dict(requestId=request['requestId'], expectedRevision=1,
        choiceId='q-small', revisionText='Small', idempotencyKey=request['requestId']))['operation']


def test_two_worker_citations_mirror_real_questions_in_order(tmp_path):
    mirror, store, path, hr, originals = cited_fixture(tmp_path)
    mirror.reconcile(OWNER)
    mirror.reconcile(OWNER)
    requests = store.requests(OWNER)
    assert len(requests) == 2
    assert [r['title'] for r in requests] == [r['title'] for r in originals]
    for r, cited in zip(requests, originals):
        assert r['scope']['markdown'] == cited['scope']['markdown']
        assert r['choices'] == [dict(ch, id='q-' + ch['id']) for ch in cited['choices']]
        p = mirror.present(r)['presentation']
        assert p['question'] == cited['title']
        assert p['details'] == cited['scope']['markdown']
        assert p['recommendedChoiceId'] == 'q-small'
        assert p['other'] is True and p['sourceRef']['id'] == 't_test'
        assert not re.search(r't_\w+|[0-9a-f]{8}-', p['question'])
        snap = json.loads(mirror.binding(OWNER, r['requestId'])['snapshot'])
        assert snap['cited_id'] == cited['requestId'] and snap['cited_revision'] == 1
        assert snap['cited_set'] == originals


@pytest.mark.parametrize('payload', ['"plain string"', '[1]', '{bad json'])
def test_malformed_unrelated_block_event_does_not_hide_questions(tmp_path, payload):
    mirror, store, path, hr, originals = cited_fixture(tmp_path)
    unrelated = dict(hr, action='Answer the unrelated question', evidence='Legacy event')
    with sqlite3.connect(path) as c:
        c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,NULL)',
                  ('t_other', 'Unrelated question', 'blocked', 'session-a', json.dumps(unrelated)))
        c.execute("INSERT INTO task_events(task_id,kind,payload) VALUES ('t_other','blocked',?)",
                  (payload,))
    mirror.reconcile(OWNER)
    requests = store.requests(OWNER)
    assert len(requests) == 3
    cited = [r for r in requests if r['scope']['sourceRefs'][0]['id'] == 't_test']
    assert [r['title'] for r in cited] == [r['title'] for r in originals]
    fallback = next(r for r in requests if r['scope']['sourceRefs'][0]['id'] == 't_other')
    assert fallback['title'] == 'Unrelated question'
    assert 'cited_set' not in json.loads(mirror.binding(OWNER, fallback['requestId'])['snapshot'])


@pytest.mark.parametrize('case', ['projection', 'owner-session'])
def test_already_visible_citation_uses_fallback_without_copy(tmp_path, case):
    mirror, store, path, hr = fixture(tmp_path)
    visible_owner = dict(OWNER, storedSessionId='session-b') if case == 'projection' else OWNER
    if case == 'projection':
        with sqlite3.connect(path) as c:
            c.execute("UPDATE tasks SET session_id='session-b'")
        mirror.reconcile(visible_owner)
        visible = store.requests(visible_owner)[0]
    else:
        visible = complete_request(title='Already visible question')
        visible.update(kind='choice', outcomes={'answer': 'Record answer'},
                       choices=[dict(id='yes', label='Yes', effect='answer')])
        visible = store.create(OWNER, visible)
    ask = dict(hr, action='Answer the cited question', evidence=visible['requestId'])
    ask.pop('choices')
    with sqlite3.connect(path) as c:
        c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,NULL)',
                  ('t_other', 'Other question', 'blocked', 'session-a', json.dumps(ask)))
        c.execute("INSERT INTO task_events(task_id,kind,payload) VALUES ('t_other','blocked','{}')")
    mirror.reconcile(OWNER)
    requests = store.requests(OWNER)
    assert len(requests) == (1 if case == 'projection' else 3)
    other = [r for r in requests if any(ref.get('id') == 't_other'
             for ref in r['scope']['sourceRefs'])]
    assert len(other) == 1
    snap = mirror._read_snapshot('default', 't_other', OWNER)
    assert 'cited_set' not in snap
    assert other == [mirror._request(OWNER, 'default', snap)]
    assert store.get(visible_owner, visible['requestId']) == visible


def test_comments_then_final_unblock_and_replays(tmp_path):
    mirror, store, path, hr, originals = cited_fixture(tmp_path)
    mirror.reconcile(OWNER)
    operations = [answer(store, r) for r in store.requests(OWNER)]
    for i, op in enumerate(operations):
        receipt = mirror.deliver(op)
        assert mirror.readback(op, receipt)
        assert mirror.deliver(op) == receipt
        with sqlite3.connect(path) as c:
            assert c.execute('SELECT status FROM tasks').fetchone()[0] == ('blocked' if i == 0 else 'ready')
            comments = c.execute('SELECT body FROM task_comments ORDER BY id').fetchall()
            assert len(comments) == i + 1
            cited = originals[i]
            assert comments[-1][0] == (f'Answer recorded in Desktop for "{cited["title"]}" '
                f'(request {cited["requestId"]} rev 1): Small [small]')
        mirror.reconcile(OWNER)
    for r in originals:
        assert store.get(r['owner'], r['requestId']) == r


def test_changed_ask_after_first_answer_is_409(tmp_path):
    mirror, store, path, hr, originals = cited_fixture(tmp_path)
    mirror.reconcile(OWNER)
    operations = [answer(store, r) for r in store.requests(OWNER)]
    mirror.deliver(operations[0])
    hr['scope'] = 'Different question'
    with sqlite3.connect(path) as c:
        c.execute('UPDATE tasks SET human_request=?', (json.dumps(hr),))
    with pytest.raises(core.ActionError) as exc:
        mirror.deliver(operations[1])
    assert exc.value.status == 409
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'blocked'
        assert c.execute('SELECT COUNT(*) FROM task_comments').fetchone()[0] == 1


@pytest.mark.parametrize('case', ['unknown', 'closed', 'foreign', 'own-choices', 'not-choice'])
def test_fallback_identity_and_markdown_unchanged(tmp_path, case):
    mirror, store, path, hr, originals = cited_fixture(tmp_path, own_choices=case == 'own-choices')
    if case != 'own-choices':
        for r in originals:
            with store._tx() as c:
                if case == 'closed':
                    c.execute("UPDATE requests SET state='withdrawn' WHERE id=?", (r['requestId'],))
                elif case in ('foreign', 'unknown'):
                    c.execute('UPDATE requests SET namespace=? WHERE id=?', ('other-install', r['requestId']))
                else:
                    body = dict(r, kind='input')
                    c.execute('UPDATE revisions SET body=? WHERE id=?', (json.dumps(body), r['requestId']))
    snap = mirror._read_snapshot('default', 't_test', OWNER)
    assert 'cited_set' not in snap
    expected = mirror._request(OWNER, 'default', snap)
    # Use the historical serializer explicitly: no new fields or prefix changes.
    encoded = lambda x: json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    assert expected['requestId'] == str(uuid.uuid5(uuid.NAMESPACE_URL,
        'hermes:kanban-ask:' + encoded([OWNER, 'default', snap])))
    markdown = '\n\n'.join(str(hr[k]) for k in ('action', 'why_human', 'scope', 'evidence') if hr.get(k))
    markdown += '\n\nSelecting an option records its value on this card and resumes it. '
    markdown += '' if hr.get('choices') else 'Archive abandons the card.'
    markdown += ' No commands run from this panel.'
    mirror.reconcile(OWNER)
    assert store.requests(OWNER) == [expected]
    assert expected['scope']['markdown'] == markdown


def test_cited_answers_are_serialized_across_concurrent_deliveries(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    mirror, store, path, hr, originals = cited_fixture(tmp_path)
    mirror.reconcile(OWNER)
    ops = [answer(store, r) for r in store.requests(OWNER)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(mirror.deliver, [ops[0], ops[1], ops[0], ops[1]]))
    assert receipts[0] == receipts[2] and receipts[1] == receipts[3]
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'ready'
        assert c.execute('SELECT COUNT(*) FROM task_comments').fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM task_events WHERE kind='unblocked'").fetchone()[0] == 1


def test_receipts_do_not_cross_block_generations(tmp_path):
    mirror, store, path, hr, originals = cited_fixture(tmp_path)
    mirror.reconcile(OWNER)
    mirror.deliver(answer(store, store.requests(OWNER)[0]))
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO task_events(task_id,kind,payload) VALUES ('t_test','blocked',?)",
                  (json.dumps({'reason': originals[1]['requestId']}),))
    old_ids = {r['requestId'] for r in store.requests(OWNER)}
    mirror.reconcile(OWNER)
    new = [r for r in store.requests(OWNER) if r['requestId'] not in old_ids]
    assert len(new) == 2
    mirror.deliver(answer(store, new[1]))
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'blocked'
    mirror.deliver(answer(store, new[0]))
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'ready'


def test_cited_other_is_informational_and_counts_as_one_answer(tmp_path):
    mirror, store, path, hr, originals = cited_fixture(tmp_path)
    mirror.reconcile(OWNER)
    first, second = store.requests(OWNER)
    op = mirror.decide_other(OWNER, dict(requestId=first['requestId'], expectedRevision=1,
        choiceId='answer-text', revisionText='Neither: use purple', idempotencyKey='other'))['operation']
    assert op['intent']['executionAuthorized'] is False
    mirror.deliver(op)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'blocked'
        assert 'Neither: use purple [answer-text]' in c.execute('SELECT body FROM task_comments').fetchone()[0]
    mirror.deliver(answer(store, second))
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'ready'
