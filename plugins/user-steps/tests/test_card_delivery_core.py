"""Real-core transaction composition; never disable delegated mutation fences."""
import sqlite3
import pytest
from test_delivery import cards
from test_decisions import OWNER, store, request, decision


@pytest.mark.parametrize('effect', ['approve', 'revise', 'decline'])
def test_real_core_card_binding_comment_metadata_atomic(tmp_path, monkeypatch, effect):
    kb = pytest.importorskip('hermes_cli.kanban_db')
    from agent.delegation_context import kanban_path_is_fenced
    path = tmp_path / 'kanban.db'
    if kanban_path_is_fenced(path):
        pytest.skip('Delegated mutation fence: run in permitted top-level process')
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    token = set_hermes_home_override(str(tmp_path))
    try:
        ref = dict(gatewayId=OWNER['gatewayId'], profile=OWNER['profile'], type='kanban', board='default', id='card')
        s = store(tmp_path); r = s.create(OWNER, request(aliases=[ref]))
        adapter = cards.QualifiedCardDelivery(home=tmp_path)
        with sqlite3.connect(path) as c:
            c.executescript(kb.SCHEMA_SQL)
            c.execute("INSERT INTO tasks (id,title,status,created_at,session_id,idempotency_key) VALUES ('card','Synthetic decision','blocked',1,?,?)",
                      (OWNER['storedSessionId'], adapter.creation_key(r)))
        adapter.bind(r, ref)
        op = s.decide(OWNER, decision(r, choice=effect, text='Add tests'))['operation']
        original = kb.add_comment
        def fail_after_comment(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('after nested core comment')
        monkeypatch.setattr(kb, 'add_comment', fail_after_comment)
        with pytest.raises(RuntimeError, match='after nested'): adapter.deliver(op)
        with sqlite3.connect(path) as c:
            assert c.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 0
            assert c.execute('SELECT state FROM user_steps_card_bindings').fetchone()[0] == 'bound'
        monkeypatch.setattr(kb, 'add_comment', original)
        receipt = adapter.deliver(op)
        assert cards.QualifiedCardDelivery(home=tmp_path).deliver(op) == receipt
        assert adapter.readback(op, receipt)
        with sqlite3.connect(path) as c:
            assert c.execute('SELECT status FROM tasks').fetchone()[0] == 'blocked'
            assert c.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 1
    finally:
        reset_hermes_home_override(token)
