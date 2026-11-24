"""Independent real-provider probes with populated actions and poisoned ambient home."""
import shutil
import sys
from pathlib import Path
import pytest
from test_explicit_home_acquisition import (api, worlds, tripwires, ctx_for, load_module,
    ROOT, payload_for, launch_label_read, populated)


@pytest.mark.parametrize('remote', [False, 'pending', 'retiring', 'delivered'])
def test_actual_populated_provider_poisoned_process_home(api, worlds, tripwires, monkeypatch, tmp_path, remote):
    home = worlds['explicit']
    reader = api.CoreReaders(home_context=ctx_for(home))
    companion = reader._user_step_api()
    core = companion._load('core')
    monkeypatch.syspath_prepend(str(ROOT.parent / 'user-steps' / 'tests'))
    from test_withdrawal import OWNER, complete_request
    with tripwires.disarmed():
        monkeypatch.setitem(OWNER, 'gatewayId', 'install:' + 'a' * 32)
        monkeypatch.setitem(OWNER, 'profile', reader._home_identity()['profile'])
        monkeypatch.setitem(OWNER, 'storedSessionId', 'chat')
        if remote:
            from test_remote_card_state_machine import setup, depart
            seed = tmp_path / 'seed'
            seed.mkdir()
            mirror, store, row, old, op = setup(seed)
            if remote in ('retiring', 'delivered'):
                depart(mirror, row, departure=False)
            if remote == 'delivered':
                from test_remote_card_state_machine import Courier
                mirror.remote.courier = Courier('done')
                depart(mirror, row, departure=False)
            import sqlite3
            source = sqlite3.connect(store.path)
            target = sqlite3.connect(home / 'user-steps' / 'actions.sqlite3')
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
        else:
            store = core.ActionStore(home / 'user-steps' / 'actions.sqlite3')
            store.create(OWNER, complete_request(title='POPULATED-REVIEW-ACTION'))
    reader.release()
    provider = load_module(ROOT / 'dashboard' / 'producer_provider.py', '_review_real_provider')
    with launch_label_read(tripwires):
        produced = provider.open_provider({'home_context': ctx_for(home)})
    sentinel = tmp_path / 'unreadable-process-home'
    sentinel.write_text('not a directory')
    for name in ('HERMES_HOME', 'HERMES_KANBAN_HOME', 'HERMES_HUB_STATE'):
        monkeypatch.setenv(name, str(sentinel))
    try:
        requests = produced.readers.user_step_requests('chat', ['chat'])
        if remote:
            assert requests and requests[0]['title'] == 'Choose remotely'
            assert ('delivery' in requests[0]) == (remote == 'retiring')
        wire = populated(produced, lambda: payload_for(['chat']))
        assert tripwires == []
        text = wire.decode() if isinstance(wire, bytes) else str(wire)
        if remote == 'retiring':
            assert 'Choose remotely' in text and 'Delivering' in text
        elif not remote:
            assert 'POPULATED-REVIEW-ACTION' in text
    finally:
        produced.close()
