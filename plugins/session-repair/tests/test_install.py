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
    assert len(calls) == 1 and calls[0][-14:] == ['cron', 'create', 'every 15m', '--name', 'session-repair',
                                                  '--no-agent', '--script', 'session_repair_tick.py', '--deliver',
                                                  'local', '--failure-deliver', 'bot-chat', '--catch-up', 'off']

    capsys.readouterr()
    assert inst.main(['--apply']) == 0
    assert 'already installed' in capsys.readouterr().out and len(calls) == 1, 'idempotent'

    assert inst.main(['--apply', '--mode', 'live']) == 0
    assert json.loads((tmp_path / 'session-repair' / 'config.json').read_text()) == {'mode': 'live'}
    assert len(calls) == 1


def test_failures_never_go_to_local(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    import pytest
    with pytest.raises(SystemExit):
        inst.main(['--failure-deliver', 'local'])
    with pytest.raises(ValueError):
        inst.cron_argv('local')
    assert inst.cron_argv('telegram')[-4:-2] == ['--failure-deliver', 'telegram']


def test_cron_argv_parses_with_cores_cron_parser():
    import pytest
    _parser = pytest.importorskip('hermes_cli._parser')
    cron = pytest.importorskip('hermes_cli.subcommands.cron')
    parser, subparsers, _chat = _parser.build_top_level_parser()
    cron.build_cron_parser(subparsers, cmd_cron=lambda args: None)
    args = parser.parse_args(inst.cron_argv()[1:])
    assert args.schedule == 'every 15m' and args.name == 'session-repair' and args.no_agent
    assert args.script == 'session_repair_tick.py' and args.deliver == 'local' and args.failure_deliver == 'bot-chat'
