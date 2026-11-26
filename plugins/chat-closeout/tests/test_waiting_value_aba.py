"""Independent review: committed ABA across input declaration and source read."""
import sqlite3
import pytest
from test_closeout import plugin
from test_waiting_inputs import world, poll, reset
from test_waiting_task_inputs import certified
from test_host_idle_skip import clock


@pytest.mark.parametrize('initial_done', [True, False])
def test_declared_values_aba_does_not_certify_intermediate_answer(certified, clock, initial_done):
    board = certified['board']
    def set_state(done):
        with sqlite3.connect(board) as conn:
            conn.execute("UPDATE tasks SET status=?, completed_at=? WHERE id='t_2'",
                         ('done' if done else 'running', 100.0 if done else None))
    def expected(done):
        return {'c1': ['t_1'], **({'c2': ['t_2']} if done else {})}
    set_state(initial_done)
    plugin.waiting_inputs()  # A, deliberately before first source pass
    set_state(not initial_done)
    assert plugin.waiting_source() == expected(not initial_done)  # B under A's declaration
    set_state(initial_done)
    plugin.waiting_inputs()  # A again, committed and observed
    observed = poll()  # bypass only the 3-second answer cache, preserve semantic memo
    assert observed == expected(initial_done)


@pytest.mark.parametrize('initial_done', [True, False])
def test_aba_entirely_inside_source_read(certified, clock, monkeypatch, initial_done):
    def state(done):
        with sqlite3.connect(certified['board']) as conn:
            conn.execute("UPDATE tasks SET status=?, completed_at=? WHERE id='t_2'",
                         ('done' if done else 'running', 100.0 if done else None))
    state(initial_done)
    plugin.waiting_inputs()
    original = plugin.read_cards
    def interleaved(*a, **kw):
        state(not initial_done)
        try:
            return original(*a, **kw)
        finally:
            state(initial_done)
    with monkeypatch.context() as m:
        m.setattr(plugin, 'read_cards', interleaved)
        assert ('c2' in plugin.waiting_source()) is not initial_done
    plugin.waiting_inputs()
    assert ('c2' in poll()) is initial_done


@pytest.mark.parametrize('initial_done', [True, False])
def test_holder_two_declarations_do_not_keep_intermediate_answer(certified, clock, monkeypatch, initial_done):
    from hermes_cli import kanban_annotation_host as host, kanban_annotation_parts as parts
    from test_waiting_inputs import ksa
    def state(done):
        with sqlite3.connect(certified['board']) as conn:
            conn.execute("UPDATE tasks SET status=?, completed_at=? WHERE id='t_2'",
                         ('done' if done else 'running', 100.0 if done else None))
    scope = ('aba-fixture',)
    source = plugin.waiting_source
    monkeypatch.setattr(ksa, '_origin_scope', lambda: scope)
    monkeypatch.setattr(ksa, '_waiting_sources', {'fixture': source})
    monkeypatch.setattr(host, '_declared', {'fixture': (source, plugin.waiting_inputs)})
    monkeypatch.setattr(parts, 'keys', lambda scope: {
        **{p: 'constant' for p in parts.ALL if p != parts.WAITING}, parts.WAITING: parts._waiting_key()})
    def refresh(interleave=False):
        forced, keys = parts.plan(scope, clock=False)
        if interleave:
            state(not initial_done)
        # Actual host prepare/fingerprint takes this second declaration before source.
        host._waiting_inputs(dict(ksa._waiting_sources))
        monkeypatch.setattr(ksa._revalidate.force_read, 'scope', scope, raising=False)
        monkeypatch.setattr(ksa._revalidate.force_read, 'parts', forced, raising=False)
        answer = parts.waiting(ksa._extra_waiting)
        parts.done(scope, keys)
        return answer
    parts.reset()
    try:
        state(initial_done)
        assert ('c2' in refresh(interleave=True)) is not initial_done
        state(initial_done)
        assert ('c2' in refresh()) is initial_done
    finally:
        parts.reset()


def test_old_core_broad_marks_are_safe_and_deliberately_lose_comment_skip(certified, clock, monkeypatch):
    from hermes_cli import kanban_task_change as tc
    monkeypatch.delattr(tc, 'settlement_marks', raising=False)
    before = plugin.waiting_inputs()
    with sqlite3.connect(certified['board']) as conn:
        conn.execute("INSERT INTO task_comments VALUES (1,'t_1','comment','agent',1)")
    assert plugin.waiting_inputs() != before
    assert plugin.waiting_source() == {'c1': ['t_1'], 'c2': ['t_2']}
