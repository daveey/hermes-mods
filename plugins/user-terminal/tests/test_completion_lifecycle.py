"""The host-local reconciler covers old jobs and unloads with its plugin."""
import threading
from types import SimpleNamespace

import pytest
from test_closed_asks import ut


def test_reconciler_waits_for_host_then_sweeps_and_stops(monkeypatch):
    from hermes_cli import plugins
    for key in ('HERMES_KANBAN_TASK', 'HERMES_KANBAN_WORKER_TASK', 'HERMES_MANAGED_WORKER'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(plugins, '_published_gateway_message_injector', None)
    monkeypatch.setattr(plugins, '_published_tui_message_injector', None)
    monkeypatch.setattr(ut, 'WATCH_INTERVAL_S', .01)
    swept = threading.Event()
    stopped = []
    monkeypatch.setattr(ut, 'sweep_jobs', swept.set)
    ut.start_reconciler(SimpleNamespace(on_unload=stopped.append))
    try:
        assert not swept.wait(.05)
        # TUI-only hosts still reconcile card outcomes; session delivery can defer.
        monkeypatch.setattr(plugins, '_published_tui_message_injector', (object(), None))
        assert swept.wait(2)
        stopped[0]()
        swept.clear()
        assert not swept.wait(.05)
    finally:
        stopped[0]()


@pytest.mark.parametrize('key', ['HERMES_KANBAN_TASK', 'HERMES_KANBAN_WORKER_TASK', 'HERMES_MANAGED_WORKER'])
def test_managed_worker_does_not_start_reconciler(monkeypatch, key):
    monkeypatch.setenv(key, 'fixture')
    def forbidden(*args):
        pytest.fail('managed worker started host lifecycle')
    ut.start_reconciler(SimpleNamespace(on_unload=forbidden))


def test_foreign_profile_job_has_no_delivery_or_missing_receipt(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(ut, '_home', lambda: tmp_path / 'owner')
    job = tmp_path / 'job'
    job.mkdir()
    (job / 'meta.json').write_text(json.dumps({'home': str(tmp_path / 'foreign'),
        'owner': {'kind': 'session', 'session_id': 'foreign-chat'}}))
    def forbidden(*args):
        pytest.fail('cross-profile delivery attempted')
    monkeypatch.setattr(ut, '_completion_module', forbidden)
    ut.reconcile_session(job, {'status': 'ran', 'exit_code': 0})
    assert not (job / 'completion-receipt.json').exists()
