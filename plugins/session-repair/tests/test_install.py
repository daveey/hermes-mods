"""install.py: the plan, --apply (wrapper, config, cron create through a fake), and idempotence."""
import json
import subprocess

from sr_helpers import PLUGIN, load

inst = load('_sr_install', PLUGIN / 'install.py')


def test_plan_apply_and_idempotence(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'plugins' / 'session-repair').mkdir(parents=True)
    (tmp_path / 'plugins' / 'session-repair' / 'run.py').write_text('')
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        (tmp_path / 'cron').mkdir(exist_ok=True)
        (tmp_path / 'cron' / 'jobs.json').write_text(json.dumps({'jobs': [{'name': 'session-repair'}]}))
        return subprocess.CompletedProcess(argv, 0, stdout='created', stderr='')
    monkeypatch.setattr(inst.subprocess, 'run', fake_run)

    assert inst.main([]) == 0
    assert 'would cron:' in capsys.readouterr().out and calls == [], 'no --apply: plan only'
    assert not (tmp_path / 'scripts').exists()

    assert inst.main(['--apply']) == 0
    script = tmp_path / 'scripts' / 'session_repair_tick.py'
    assert script.read_text() == inst.WRAPPER and "'session-repair', 'run.py'" in inst.WRAPPER
    assert json.loads((tmp_path / 'session-repair' / 'config.json').read_text()) == {'mode': 'shadow'}
    assert len(calls) == 1 and calls[0][-12:] == ['cron', 'create', 'every 15m', '--name', 'session-repair',
                                                  '--no-agent', '--script', 'session_repair_tick.py', '--deliver',
                                                  'local', '--catch-up', 'off']

    capsys.readouterr()
    assert inst.main(['--apply']) == 0
    assert 'already installed' in capsys.readouterr().out and len(calls) == 1, 'idempotent'

    assert inst.main(['--apply', '--mode', 'live']) == 0
    assert json.loads((tmp_path / 'session-repair' / 'config.json').read_text()) == {'mode': 'live'}
    assert len(calls) == 1
