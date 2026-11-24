"""The panel's exact producer-safe manifest, verified by core's verifier and booted through the shared owner."""
import copy
import importlib.util
import os
from pathlib import Path
import sys

import pytest

manifest_mod = pytest.importorskip('hermes_cli.serve_producer_manifest')
broker = pytest.importorskip('hermes_cli.serve_producer_broker')

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / 'dashboard'


def load_api():
    name = '_panel_manifest_fixture'
    spec = importlib.util.spec_from_file_location(name, DASH / 'plugin_api.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_panel_spec_carries_a_complete_verifiable_manifest(monkeypatch):
    for name in ('HERMES_PANEL_COALESCE', 'HERMES_PANEL_DUTY', 'HERMES_PANEL_SKIP_QUEUE_POSITION', 'HERMES_FACTS_PANEL'):
        monkeypatch.delenv(name, raising=False)
    spec = load_api()._panel_provider_spec()
    manifest = spec['manifest']
    assert set(manifest) == {'version', 'providers', 'callbacks', 'modes', 'facts'}
    assert manifest['callbacks'] == {'done': [], 'waiting': []}
    assert manifest['facts'] == {'HERMES_FACTS_PANEL': True}
    assert manifest['modes'] == {'HERMES_PANEL_COALESCE': True, 'HERMES_PANEL_DUTY': True,
                                 'HERMES_PANEL_SKIP_QUEUE_POSITION': True}
    declared = manifest_mod.verify_declared(manifest, DASH)
    assert 'producer_provider.py' in declared and 'plugin_api.py' not in declared  # legacy module undeclared
    assert declared['producer_provider.py'] == spec['sha256']
    on_disk = {p.name for p in DASH.glob('*.py')} - {'plugin_api.py'}
    assert set(declared) == on_disk


def test_effective_switches_are_what_the_manifest_declares(monkeypatch):
    monkeypatch.setenv('HERMES_FACTS_PANEL', '0')
    monkeypatch.setenv('HERMES_PANEL_DUTY', 'off')
    manifest = load_api()._panel_provider_spec()['manifest']
    assert manifest['facts'] == {'HERMES_FACTS_PANEL': False}
    assert manifest['modes']['HERMES_PANEL_DUTY'] is False
    assert manifest['modes']['HERMES_PANEL_COALESCE'] is True


def test_any_tamper_of_the_panel_manifest_refuses(monkeypatch):
    protocol = pytest.importorskip('hermes_cli.serve_producer_protocol')
    manifest = load_api()._panel_provider_spec()['manifest']
    broken = copy.deepcopy(manifest)
    broken['providers']['feed']['sha256'] = '0' * 64
    with pytest.raises(protocol.ProtocolError, match='digest mismatch'):
        manifest_mod.verify_declared(broken, DASH)
    missing = copy.deepcopy(manifest)
    del missing['callbacks']
    with pytest.raises(protocol.ProtocolError):
        manifest_mod.verify_declared(missing, DASH)


def test_real_provider_loads_only_declared_modules(tmp_path):
    """Import the real provider in a clean interpreter, then run core's post-import check on what it loaded."""
    import subprocess
    probe = tmp_path / 'probe.py'
    probe.write_text(
        'import importlib.util, json, sys\n'
        f'from pathlib import Path\nDASH = Path({str(DASH)!r})\n'
        'spec = importlib.util.spec_from_file_location("_scoped_provider", DASH / "producer_provider.py")\n'
        'mod = importlib.util.module_from_spec(spec); sys.modules["_scoped_provider"] = mod\n'
        'spec.loader.exec_module(mod)\n'
        'from hermes_cli.serve_producer_manifest import verify_loaded\n'
        f'manifest = json.loads({__import__("json").dumps(__import__("json").dumps(load_api()._panel_provider_spec()["manifest"]))})\n'
        'verify_loaded(manifest, DASH, allowed=("producer_provider.py",))\n'
        'print("OK")\n')
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    done = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, env=env, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith('OK'), done.stderr


def test_real_provider_boots_through_the_shared_owner_with_the_manifest(tmp_path, monkeypatch):
    """Real exec: manifest verified before import and after, two serves attach to ONE exec child."""
    import sqlite3
    import time
    from hermes_cli.serve_producer_admission import REQUIRED_COLLECTORS, AdmissionCut, CollectorEvidence, LaunchGate
    from hermes_cli.serve_producer_runtime import ProjectionRelay
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('HERMES_KANBAN_DB', raising=False)
    with sqlite3.connect(tmp_path / 'state.db') as db:
        db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,parent_session_id TEXT,outcome_json TEXT)')
    spec = load_api()._panel_provider_spec()
    runtime = tmp_path / 'runtime'
    relays = []

    def attach():
        relay = ProjectionRelay(spec, home=tmp_path, runtime=runtime, generation='unused')
        gate = LaunchGate.acquire(runtime)
        try:
            collectors = tuple((n, CollectorEvidence((f'{n}:ok',), ())) for n in sorted(REQUIRED_COLLECTORS))
            cut = AdmissionCut(boot_id='boot', generation='a' * 64, installation=str(tmp_path), census=None,
                               collectors=collectors, classifications=(), legacy_candidates=(), retired=(),
                               errors=(), _acquisition=gate._acquisition)
            relay.start_for_production(cut=cut, gate=gate, ready_timeout=90)
        finally:
            gate.close()
        relays.append(relay)
        return relay

    try:
        first, second = attach(), attach()
        assert first.broker_pid == second.broker_pid != os.getpid()
        assert first.ping()['child_pid'] == second.ping()['child_pid']
        assert broker.read_owner(runtime)['pid'] == first.broker_pid
    finally:
        for relay in relays[1:]:
            relay.close(timeout=10)
        if relays:
            assert relays[0].retire(timeout=20)
    assert broker.read_owner(runtime) is None
