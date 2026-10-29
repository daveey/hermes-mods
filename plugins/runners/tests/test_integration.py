import importlib
import json
from dataclasses import asdict


def test_registration_has_real_tools_and_no_inventory_side_effects(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    plugin=importlib.import_module('plugins.runners')
    tools = []
    plugin.register(SimpleNamespace(register_tool=lambda **kw: tools.append(kw)))
    assert {t['name'] for t in tools} == {'runners_list', 'runners_inspect', 'runners_refresh', 'runners_repair', 'runners_verify', 'runners_operate', 'runners_receipt',
                                          'runners_run', 'runners_job'}
    # Repair chats are pinned to the 'runners' toolset; dispatch is deliberately outside it.
    assert {t['name']: t['toolset'] for t in tools}['runners_run'] == 'runners_dispatch'
    assert all(callable(t['handler']) for t in tools)
    assert list(tmp_path.iterdir())==[]


def test_default_store_path_and_durable_health_state(tmp_path,monkeypatch):
    from plugins.runners.store import Store
    from plugins.runners.health import State,Sample,advance
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    s=advance(State(),Sample('a',100,'offline'),now=100)
    with Store() as db:
        db.save_health('runner_'+'1'*16,s)
        assert db.path==tmp_path/'runners/state.sqlite'
    with Store() as db:
        assert db.load_health('runner_'+'1'*16)==s
        recovered=advance(db.load_health('runner_'+'1'*16),Sample('b',130,'offline'),now=130)
        assert recovered.incident_open


def test_stable_runner_identity_is_not_display_name():
    from plugins.runners.models import runner_id
    assert runner_id('github_actions','https://api.github.com','scope_'+'1'*16,'12')==runner_id('github_actions','https://api.github.com','scope_'+'1'*16,'12')
    assert runner_id('github_actions','https://api.github.com','scope_'+'1'*16,'12')!=runner_id('github_actions','https://api.github.com','scope_'+'1'*16,'13')
