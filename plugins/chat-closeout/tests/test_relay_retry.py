"""Durable retry admission across backend processes and moved-session routing."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_retry', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
core = plugin.core


def setup(tmp_path, monkeypatch, result='refused'):
    led = core.Ledger(tmp_path / 'ledger.db')
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, '_lineage', lambda sid: [sid])
    monkeypatch.setattr(plugin, '_moved_owner', lambda sid: None, raising=False)
    cards = {'ask': {'status': 'blocked', 'block_kind': 'needs_input', 'human_request': {
        'action': 'Choose the account', 'why_human': 'Only you know', 'interaction': 'reply',
        'scope': 'account', 'evidence': 'Two accounts'}}, 'work': {'status': 'running'}}
    led.write('chat', batch=list(cards), open=list(cards))
    calls = []
    def wake(*args, **kwargs):
        calls.append(args)
        return result
    def tick(now):
        return plugin.wake_tick(now=now, wake=wake, unseen={}, last_user=lambda _: 0, read=lambda _: cards)
    return led, calls, tick


def test_refused_waits_ten_minutes_across_ledger_instances(tmp_path, monkeypatch):
    led, calls, tick = setup(tmp_path, monkeypatch)
    tick(1000)
    monkeypatch.setattr(plugin, 'ledger', lambda: core.Ledger(led.path))
    for now in (1030, 1300, 1599):
        assert not tick(now)
    tick(1600)
    assert len(calls) == 2


def test_busy_retries_next_tick(tmp_path, monkeypatch):
    _, calls, tick = setup(tmp_path, monkeypatch, 'busy')
    tick(1000)
    tick(1030)
    assert len(calls) == 2


def test_moved_chat_handed_off_once_never_woken_locally(tmp_path, monkeypatch):
    led, calls, tick = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(plugin, '_moved_owner', lambda sid: 'remote', raising=False)
    forwards = []
    monkeypatch.setattr(plugin, '_handoff_ask', lambda *args: forwards.append(args) or 'forwarded', raising=False)
    tick(1000)
    for now in (1030, 1600, 5000):
        tick(now)
    assert calls == []
    assert len(forwards) == 1
    assert led.get('chat')['wake_result'] == 'forwarded'


def test_error_also_backs_off(tmp_path, monkeypatch):
    _, calls, tick = setup(tmp_path, monkeypatch, 'error')
    tick(1000)
    tick(1030)
    assert len(calls) == 1
    tick(1600)
    assert len(calls) == 2
