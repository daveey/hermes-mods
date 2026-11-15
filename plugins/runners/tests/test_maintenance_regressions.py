"""Behavioral regressions for maintenance admission and durable scheduling."""
from types import SimpleNamespace

import pytest

from plugins.runners.config import ConfigFile
from plugins.runners.providers.base import Credential, HTTPResponse
from plugins.runners.providers.github_actions import _Failure
from plugins.runners.scripts import reconcile_scopes as cli
from plugins.runners.scripts.maintenance_schedule import MaintenanceSchedule
from plugins.runners.tests.test_maintenance_budget import harness
from plugins.runners.tests.test_maintenance_schedule import fixture


def test_spacing_overrun_refuses_transport():
    api, calls, durable = harness()
    # Admission passes (no existing debt), but one more request would push the
    # shared debt 1000s out, past the 384s maintenance bound.
    api.restore_state({'shared': {'remaining': 2503, 'reset': 3000, 'next_at': 1000}})
    with pytest.raises(_Failure, match='pacing'):
        api._get(Credential('shared', 'token'), '/repos/example/repo', metadata=True)
    assert calls == []
    assert durable and all(state['shared']['next_at'] == 1000 for state in durable)
    assert api.export_state()['shared']['remaining'] == 2503


def test_spacing_projects_the_imminent_request_charge():
    api, calls, durable = harness()
    # Spacing must be projected after the imminent decrement: 760s/1 overruns
    # the 384s bound, whereas the pre-decrement 760s/2 = 380s would not.
    api.restore_state({'shared': {'remaining': 2502, 'reset': 1760, 'next_at': 1000}})
    with pytest.raises(_Failure, match='pacing'):
        api._get(Credential('shared', 'token'), '/repos/example/repo', metadata=True)
    assert calls == []
    assert durable and all(state['shared']['next_at'] == 1000 for state in durable)


def test_schedule_prunes_obsolete_identity():
    state = {'obsolete': {'at': 1}}
    schedule = MaintenanceSchedule(fixture(0), {}, state, 100000)
    schedule.prune()
    assert state == {}


def test_future_timestamps_have_equal_current_age():
    schedule = MaintenanceSchedule(fixture(0), {},
                                   {'a': {'at': 900000}, 'b': {'at': 800000}}, 100000)
    assert schedule.last('a') == schedule.last('b') == 100000


def test_successful_apply_fences_a_second_fresh_rename(tmp_path, monkeypatch):
    source = ConfigFile(tmp_path / 'config.yaml')
    config = source.update(fixture(1), expected_revision=0, expected_owner_epoch=1)
    args = SimpleNamespace(state_dir=str(tmp_path), apply=True, confirmation_interval=3600)
    now = [100000]
    monkeypatch.setattr(cli.time, 'time', lambda: now[0])

    class Broker:
        def __init__(self):
            self.calls = []
            self.target = 'example/first'

        def __call__(self, ref):
            return Credential('shared', 'fictional-token')

        def transport(self, method, url, **kwargs):
            self.calls.append(url)
            return HTTPResponse(200, {}, {'id': 1, 'full_name': self.target})

    broker = Broker()
    first = cli._run(args, source, config, {}, broker, [])
    assert first['applied']
    saved = source.load()
    assert saved['scopes'][0]['name'] == 'example/first'
    now[0] += 3600
    broker.target = 'example/second'
    second = cli._run(args, source, saved, {}, broker, [])
    assert broker.calls[-1].endswith('/repos/example/first')
    assert second['changes'][0]['new'] == 'example/second'
    assert not second['applied']
    assert source.load() == saved


def test_invalid_discovery_rejected_before_any_provider_work():
    calls = []

    def transport(*args, **kwargs):
        calls.append(args)
        return HTTPResponse(200, {}, [])

    with pytest.raises(ValueError, match='invalid_discovery'):
        cli.reconcile(fixture(0), {}, transport,
                      lambda _: Credential('shared', 'token'),
                      discover_org=[('bad/name', 'test')], schedule={}, now=100000)
    assert calls == []
