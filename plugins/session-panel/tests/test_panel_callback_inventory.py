"""Configured callbacks may never silently turn into fresh-empty child output.

This proves a fail-closed boundary, not successful callback bootstrap; the safe registrar/per-home execution
is a separate pending gate. These tests use actual core registration, real production factory and real exec.
"""
import importlib.util
import sys

import pytest

callbacks = pytest.importorskip('hermes_cli.serve_producer_callbacks')
from test_panel_provider_manifest import load_api, _scratch_home, _complete_cut


def configured(tmp_path, monkeypatch):
    from hermes_cli import kanban_annotation_host as host, kanban_session_activity as activity
    for owner, attr in ((activity, '_done_checks'), (activity, '_waiting_sources'),
                        (host, '_declared'), (host, '_event_sources'), (host, '_done_event_sources')):
        monkeypatch.setattr(owner, attr, {})
    monkeypatch.setattr(host, 'wake_holder', lambda: None)
    path = tmp_path / 'safe_callbacks.py'
    path.write_text('def done(card): return True\ndef waiting(): return {}\n'
                    'def inputs(): return []\ndef watch(): return []\n')
    spec = importlib.util.spec_from_file_location('_configured_panel_callbacks', path)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, mod)
    spec.loader.exec_module(mod)
    activity.register_done_check('configured-done', mod.done, watch_paths=mod.watch)
    activity.register_waiting_source('configured-waiting', mod.waiting, inputs=mod.inputs)
    return activity, host, mod


def test_actual_factory_captures_actual_registration_and_companion(tmp_path, monkeypatch):
    _scratch_home(tmp_path, monkeypatch)
    configured(tmp_path, monkeypatch)
    relay = load_api().make_panel_relay(tmp_path, tmp_path / 'runtime')
    expected = relay.config['provider']['manifest']['callback_registry']
    assert set(expected['done']) == {'configured-done'}
    assert set(expected['waiting']) == {'configured-waiting'}
    assert expected['done']['configured-done']['watch_paths']['qualname'] == 'watch'
    assert expected['waiting']['configured-waiting']['inputs']['qualname'] == 'inputs'
    assert expected == callbacks.capture()


def test_real_exec_missing_configured_callbacks_never_publishes_ready(tmp_path, monkeypatch):
    from hermes_cli.serve_producer_admission import LaunchGate
    from hermes_cli import serve_producer_broker as broker
    _scratch_home(tmp_path, monkeypatch)
    configured(tmp_path, monkeypatch)
    runtime = tmp_path / 'runtime'
    relay = load_api().make_panel_relay(tmp_path, runtime)
    gate = LaunchGate.acquire(runtime)
    try:
        with pytest.raises(broker.OwnerUnavailable):
            relay.start_for_production(cut=_complete_cut(gate, tmp_path), gate=gate, ready_timeout=15)
    finally:
        gate.close()
        relay.close(timeout=10)
    assert broker.read_owner(runtime) is None
