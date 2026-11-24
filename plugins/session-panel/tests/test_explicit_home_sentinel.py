"""Paired explicit-home readers fail closed with a poisoned process home."""
from __future__ import annotations

import json

import pytest

import test_explicit_home_acquisition as helpers

api, worlds, tripwires = helpers.api, helpers.worlds, helpers.tripwires
ctx_for = helpers.ctx_for


def test_paired_readers_ignore_unreadable_process_home(api, worlds, tripwires, monkeypatch, tmp_path):
    home = worlds['explicit']
    reader = api.CoreReaders(home_context=ctx_for(home))
    companion = reader._user_step_api()
    core = companion._load('core')
    with tripwires.disarmed():
        core.ActionStore(path=home / 'user-steps' / 'actions.sqlite3')
    sentinel = tmp_path / 'not-a-home'
    sentinel.write_text('Not a directory: any ambient store lookup must fail')
    monkeypatch.setenv('HERMES_HOME', str(sentinel))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(sentinel))
    monkeypatch.setenv('HERMES_HUB_STATE', str(sentinel))
    from hermes_cli import kanban_chat_links as links
    from hermes_cli.kanban_execution_observations import add_cards
    try:
        assert reader.user_step_requests('chat', ['chat']) == []
        assert reader.linked(['chat'])
        detail = reader.card(worlds['task_id'], 'default')['detail']
        assert detail['queue_unavailable']['state'] == 'unavailable'
        assert detail['task']['title'] == 'EXPLICIT-DATA'
        assert reader.decision_inputs('chat', ['chat'])['requests'] == []
        result = reader.panel_inputs('chat', ['chat'])
        assert result['input_errors']  # unsupported runtime inventory is never certified empty
        assert links.replica_cards(['chat'], 'gw-explicit', replicas=[], profile='default', origin_cards=[]) == []
        rows = add_cards([{'id': 't_explicit', 'board': 'default', 'status': 'ready'}],
                         gateway='gw-explicit', profile='default')
        assert rows[0]['execution_observations'][0]['identity']['gateway'] == 'gw-explicit'
        assert 'chat' in reader.reconcile_sources(['chat'])
        assert tripwires == []
    finally:
        reader.release()


@pytest.mark.parametrize('config', ['[1,2]', 'kanban: [unterminated', '{"kanban": []}',
                                    '{"kanban":{"sync":[]}}', '{"kanban":{"sync":{"enabled":"yes"}}}',
                                    '{"kanban":{"sync":{"interval_seconds":-1}}}'])
def test_malformed_explicit_config_never_disables_sources(api, worlds, config):
    home = worlds['explicit']
    (home / 'config.yaml').write_text(config)
    with pytest.raises(Exception):
        api._explicit_home_mod().parse_home_sync_config(home)


def test_missing_config_is_optional_but_unreadable_is_not(api, worlds):
    home = worlds['explicit']
    config = home / 'config.yaml'
    config.unlink(missing_ok=True)
    assert api._explicit_home_mod().parse_home_sync_config(home) == {}
    config.mkdir()
    with pytest.raises(OSError):
        api._explicit_home_mod().parse_home_sync_config(home)


def test_explicit_board_selection_and_outside_store_refusal(api, worlds):
    home = worlds['explicit']
    (home / 'config.yaml').write_text(json.dumps({'kanban': {'sync': {'boards': []}}}))
    reader = api.CoreReaders(home_context=ctx_for(home))
    try:
        assert reader.boards() == []
        with pytest.raises(api._home_context_mod().UnsupportedHomeAdapter):
            reader.conn(worlds['ambient'] / 'state.db')
    finally:
        reader.release()


def test_bound_public_action_read_requires_complete_identity(api, worlds, tripwires):
    reader = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    companion = reader._user_step_api()
    try:
        with pytest.raises(Exception):
            companion.panel_requests(worlds['explicit'], 'chat', ['chat'], bound=True, reconcile=False)
        with pytest.raises(Exception):
            companion.panel_requests_many(worlds['explicit'], {'chat': ['chat']}, bound=True)
        assert tripwires == []
    finally:
        reader.release()
