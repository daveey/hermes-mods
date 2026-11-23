"""Real core refresh -> panel reducer -> mounted renderer acceptance, in a disposable home."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def cases():
    core = os.environ.get('HERMES_CORE_SOURCE')
    if not core:
        return {}
    sys.path.insert(0, core)
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location('status_panel_fixture', root / 'plugins/session-panel/tests/test_model.py')
    assert spec is not None and spec.loader is not None
    f = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(f)
    result = {}
    with tempfile.TemporaryDirectory(prefix='panel-status-', dir=os.environ.get('TMPDIR')) as home:
        with patch.dict(os.environ, {'HERMES_HOME': home}):
            import hermes_state_outcome as so
            from hermes_state import SessionDB
            from hermes_cli import kanban_session_activity as ksa
            db = SessionDB(db_path=Path(home) / 'state.db')
            try:
                db.create_session('chat', source='desktop')
                db.append_message('chat', role='user', content='Check the portable work')
                db.append_message('chat', role='assistant', content='The linked work is pending')
                so._reset_hooks_for_tests()
                row = f.task(status='running', stale=True)
                reads = [row]
                error = [None]
                def cards(*args, **kwargs):
                    if error[0]:
                        raise error[0]('linked cards unavailable')
                    return reads
                def capture(name):
                    outcome = so.refresh_outcome('chat', 'card_change', db=db)
                    model = f.model.reduce_panel(f.bundle(*reads, outcome_json=outcome), now=f.NOW, projector=f.project)
                    result[name] = {'model': model, 'now': f.NOW, 'status': outcome['status']}
                with (
                    patch.object(ksa, 'session_outcome_tasks', cards),
                    patch.object(ksa, 'active_task_counts', return_value={}),
                    patch.object(ksa, 'blocked_task_counts', return_value={}),
                    patch.object(db, 'session_turn_lease_owner', return_value=None) as lease,
                ):
                    capture('stale_running')
                    row.update(stale=False, partial_source_unknown='origin_ledger_unavailable', status='done')
                    capture('partial_done')
                    from hermes_cli.fact_remote_links import LinksUnavailable
                    for exc in (LinksUnavailable, TimeoutError):
                        error[0] = exc
                        capture(exc.__name__)
                    # Expired writer authority cannot turn unavailable linked work into running.
                    lease.return_value = {'holder': 'old-writer', 'expires_at': 1.0}
                    capture('expired_lease')
                    lease.return_value = None
                    error[0] = None
                    row.update(status='running', stale=False, partial_source_unknown=None)
                    capture('recovered_running')
                    # Regressed evidence after recovery must not preserve the old running verdict.
                    row['stale'] = True
                    capture('new_failure_after_recovery')
                    reads.clear()  # the authoritative active list drops a confirmed terminal card
                    capture('recovered_done')
                    reads.append(row)
                    from tui_gateway.turn_marker import record_turn_start, clear_turn_marker
                    record_turn_start(home, 'chat', 'working locally')
                    try:
                        capture('active_local_turn')
                    finally:
                        clear_turn_marker(home, 'chat')
            finally:
                db.close()
                so._reset_hooks_for_tests()
    return result


if __name__ == '__main__':
    with contextlib.redirect_stdout(sys.stderr):
        result = cases()
    print(json.dumps(result))
