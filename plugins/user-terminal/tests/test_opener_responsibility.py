"""Caller-context routing; no live launchd or Apple Events."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location('ut_route', Path(__file__).parents[1] / '__init__.py')
ut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ut)


@pytest.mark.parametrize('entry,remote', [('desktop-ssh', True), ('gateway-tui', False), ('kanban-worker', False)])
def test_entry_path_records_and_routes_responsibility(tmp_path, monkeypatch, entry, remote):
    signed = tmp_path / 'Trusted.app/Contents/MacOS/python'
    signed.parent.mkdir(parents=True)
    signed.touch()
    monkeypatch.setattr(ut, '_signed_opener_python', lambda: signed, raising=False)
    diag = {'responsible_process': {'path': '/usr/libexec/sshd-session' if remote else str(signed)}}
    monkeypatch.setattr(ut, 'osa_diagnosis', lambda: diag)
    relay = Mock(return_value='relayed')
    monkeypatch.setattr(ut, '_open_via_launchd', relay, raising=False)
    monkeypatch.setattr(ut, 'terminal_order', lambda: ['iterm'])
    direct = Mock(return_value='iTerm')
    monkeypatch.setattr(ut, '_open_iterm', direct)
    assert ut.open_window(tmp_path) == ('relayed' if remote else 'iTerm')
    import json
    record = json.loads((tmp_path / 'opener-caller.json').read_text())
    assert record['responsible_process'] == diag['responsible_process']
    assert relay.call_count == int(remote)
    assert direct.call_count == int(not remote)


def test_relay_failure_keeps_diagnosis_and_does_not_try_untrusted_direct(tmp_path, monkeypatch):
    signed = tmp_path / 'python'
    monkeypatch.setattr(ut, '_signed_opener_python', lambda: signed, raising=False)
    diag = {'responsible_process': {'path': '/usr/libexec/sshd-session'}}
    monkeypatch.setattr(ut, 'osa_diagnosis', lambda: diag)
    monkeypatch.setattr(ut, '_open_via_launchd', Mock(side_effect=ut.OpenFailed('relay failed', diag)), raising=False)
    direct = Mock(return_value='iTerm')
    monkeypatch.setattr(ut, 'terminal_order', lambda: ['iterm'])
    monkeypatch.setattr(ut, '_open_iterm', direct)
    with pytest.raises(ut.OpenFailed, match='relay failed') as exc:
        ut.open_window(tmp_path)
    assert exc.value.diagnosis == diag
    direct.assert_not_called()


def test_launchd_is_one_shot_scrubbed_and_removed(tmp_path, monkeypatch):
    import json
    import plistlib
    monkeypatch.setenv('HERMES_KANBAN_TASK', 'worker-fixture')
    monkeypatch.setenv('HERMES_USER_TERMINAL', 'terminal')
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == 'bootstrap':
            data = plistlib.loads(Path(argv[-1]).read_bytes())
            assert data['KeepAlive'] is False and data['RunAtLoad'] is True
            assert 'HERMES_KANBAN_TASK' not in data['EnvironmentVariables']
            assert data['EnvironmentVariables']['HERMES_USER_TERMINAL'] == 'terminal'
            assert data['ProgramArguments'][2:] == ['--open-window', str(tmp_path)]
            (tmp_path / 'opener-result.json').write_text(json.dumps({'ok': True, 'terminal': 'iTerm'}))
        return Mock(returncode=0)
    monkeypatch.setattr(ut.subprocess, 'run', run)
    assert ut._open_via_launchd(tmp_path, Path('/trusted/python'), {}) == 'iTerm'
    assert [c[1] for c in calls] == ['bootstrap', 'bootout']


def test_helper_refuses_wrong_responsibility(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(ut, '_signed_opener_python', lambda: Path('/trusted/python'))
    monkeypatch.setattr(ut, 'osa_diagnosis', lambda: {'responsible_process': {'path': '/sshd'}})
    direct = Mock()
    monkeypatch.setattr(ut, '_open_window_direct', direct)
    assert ut.opener_main(tmp_path) == 1
    result = json.loads((tmp_path / 'opener-result.json').read_text())
    assert result['ok'] is False
    assert result['diagnosis']['responsible_process']['path'] == '/sshd'
    direct.assert_not_called()


def test_bootstrap_failure_removes_label_and_preserves_diagnosis(tmp_path, monkeypatch):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == 'bootstrap':
            raise ut.subprocess.CalledProcessError(1, argv)
    monkeypatch.setattr(ut.subprocess, 'run', run)
    diag = {'responsible_process': {'status': 'unknown'}}
    with pytest.raises(ut.OpenFailed) as exc:
        ut._open_via_launchd(tmp_path, Path('/trusted/python'), diag)
    assert exc.value.diagnosis == diag
    assert [c[1] for c in calls] == ['bootstrap', 'bootout']


@pytest.mark.parametrize('started', [True, False])
def test_early_runner_or_timeout_uses_helper_evidence_and_cleans(tmp_path, monkeypatch, started):
    import json
    from types import SimpleNamespace
    diag = {'responsible_process': {'status': 'known', 'pid': 123, 'path': '/trusted/python', 'name': 'python'}}
    (tmp_path / 'opener-responsibility.json').write_text(json.dumps(diag))
    ticks = iter([0, 1 if started else 100])
    monkeypatch.setattr(ut, 'time', SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None))
    monkeypatch.setattr(ut, 'window_started', lambda _: started)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == 'bootout':
            raise ut.subprocess.TimeoutExpired(argv, 10)
    monkeypatch.setattr(ut.subprocess, 'run', run)
    if started:
        assert ut._open_via_launchd(tmp_path, Path('/trusted/python'), {}) == 'signed app (runner started)'
    else:
        with pytest.raises(ut.OpenFailed, match='signed opener timed out') as exc:
            ut._open_via_launchd(tmp_path, Path('/trusted/python'), {})
        assert exc.value.diagnosis == diag
    assert [c[1] for c in calls] == ['bootstrap', 'bootout']
