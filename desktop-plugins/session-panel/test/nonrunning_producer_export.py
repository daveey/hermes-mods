"""Real disposable refusal producers for the mounted Node contract.

Invoked explicitly by nonrunning.test.mjs through core's canonical test runner.
The JUnit destination binds the export directory without inherited environment
variables (the canonical runner correctly clears those).
"""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
producer_file = HERE.parents[3] / 'plugins/session-panel/tests/test_nonrunning_producer.py'
lifecycle_file = producer_file.with_name('test_nonrunning_lifecycle.py')
lifecycle_spec = importlib.util.spec_from_file_location('mounted_lifecycle_producer', lifecycle_file)
assert lifecycle_spec is not None and lifecycle_spec.loader is not None
lifecycle = importlib.util.module_from_spec(lifecycle_spec)
lifecycle_spec.loader.exec_module(lifecycle)
spec = importlib.util.spec_from_file_location('mounted_nonrunning_producer', producer_file)
assert spec is not None and spec.loader is not None
producer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(producer)
svc = producer.svc
gws = producer.gws
kanban_home = producer.kanban_home
enforced_accounting = producer.enforced_accounting


def export(request, bundle, now, name, *, waiting=False, summary=None):
    xml = request.config.option.xmlpath
    assert xml, 'mounted harness must provide its owned output directory via --junitxml'
    models = {key: {'model': producer.panel.reduce_panel(bundle, now=clock, wire=True), 'now': clock}
              for key, clock in [('current', now), ('expired', now + 181)]}
    current = models['current']['model']['sections']
    if waiting:
        assert current['ready'] and not current['stuck'] and not current['needs_you']
        assert summary in current['ready'][0]['detail']
        models['expected'] = {'waiting': True, 'summary': summary}
    else:
        assert current['stuck']
    Path(xml).with_name(name + '.json').write_text(json.dumps(models))


def test_local_export(kanban_home, request):
    with producer.kbc.connect_closing() as conn:
        bundle, now = producer.actual_refusal(conn)
        export(request, bundle, now, 'local')


def test_transport_export(gws, request):
    bundle, now = producer.remote_refusal(gws)
    export(request, bundle, now, 'transport')


@pytest.mark.parametrize('code,summary', [
    ('gateway_draining', 'Observing dispatcher: The gateway is draining'),
    ('update_admission_hold', 'Observing dispatcher: An update admission hold is active'),
])
def test_local_drain_export(kanban_home, monkeypatch, request, code, summary):
    with producer.kbc.connect_closing() as conn:
        bundle, now = producer.actual_drain(conn, monkeypatch, code=code)
        export(request, bundle, now, 'local-' + code, waiting=True, summary=summary)


@pytest.mark.parametrize('code,summary', [
    ('gateway_draining', 'Observing dispatcher: The gateway is draining'),
    ('update_admission_hold', 'Observing dispatcher: An update admission hold is active'),
])
def test_transport_drain_export(gws, monkeypatch, request, code, summary):
    bundle, now = producer.remote_drain(gws, monkeypatch, code)
    export(request, bundle, now, 'transport-' + code, waiting=True, summary=summary)


def test_local_start_gate_export(kanban_home, monkeypatch, request):
    with producer.kbc.connect_closing() as conn:
        bundle, now = producer.actual_start_gate(conn, monkeypatch)
        export(request, bundle, now, 'local-start_gate', waiting=True,
               summary='Observing dispatcher: A plugin start gate is holding this start')


def test_transport_start_gate_export(gws, monkeypatch, request):
    bundle, now = producer.remote_start_gate(gws, monkeypatch)
    export(request, bundle, now, 'transport-start_gate', waiting=True,
           summary='Observing dispatcher: A plugin start gate is holding this start')


def test_capacity_export(kanban_home, request):
    with producer.kbc.connect_closing() as conn:
        bundle, now = lifecycle.actual_capacity(conn)
        export(request, bundle, now, 'capacity', waiting=True,
               summary='Observing dispatcher: Waiting for worker capacity')


@pytest.mark.parametrize('kind,summary', [
    ('schedule', 'left (until'), ('human', 'Reply A or B'),
    ('startup', 'worker could not be launched'),
    ('unobserved_review', 'execution reason unknown'),
])
def test_lifecycle_export(kanban_home, request, tmp_path, kind, summary):
    with producer.kbc.connect_closing() as conn:
        if kind == 'startup':
            bundle, now = lifecycle.actual_startup(conn, tmp_path / 'workspace')
        else:
            bundle, now = getattr(lifecycle, 'actual_' + kind)(conn)
        model = producer.panel.reduce_panel(bundle, now=now, wire=True)
        # Transport envelope supplied by Feed on the real wire (not an execution clock).
        model['freshness'] = {'trusted': True, 'computed_at': bundle['computed_at'], 'ttl_s': 60}
        assert summary in str(model)
        value = {'current': {'model': model, 'now': now},
                 'expected': {'lifecycle': kind, 'summary': summary}}
        Path(request.config.option.xmlpath).with_name(kind + '.json').write_text(json.dumps(value))
        if kind == 'schedule':
            # A canonical own-turn status is independent from this real parked task.
            fixture_spec = importlib.util.spec_from_file_location('header_fixture', producer_file.with_name('test_model.py'))
            assert fixture_spec is not None and fixture_spec.loader is not None
            fixture = importlib.util.module_from_spec(fixture_spec)
            fixture_spec.loader.exec_module(fixture)
            outcome = fixture.canonical_outcome('running')
            outcome['status']['presentation']['reason_codes'] = ['own_turn_active']
            bundle['outcome_json'] = outcome
            active = producer.panel.reduce_panel(bundle, now=now, wire=True)
            active['freshness'] = model['freshness']
            assert active['header']['counts']['running'] == 0
            value = {'current': {'model': active, 'now': now}, 'expected': {
                'lifecycle': 'chat_activity', 'summary': summary,
                'header': 'chat active · task execution shown below'}}
            Path(request.config.option.xmlpath).with_name('chat_activity.json').write_text(json.dumps(value))

