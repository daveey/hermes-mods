"""Lifecycle probe wire contract, with no live SSH or service access."""
import json
import pytest
from plugins.runners.probes import ssh

REF = 'service_' + 'a'*16


def spec(sources=None):
    return ssh.ProbeSpec('linux', {REF: 'example@1.service'}, '/', lifecycle_sources=sources or {REF: {'intentional_drain': True}})


def test_render_lifecycle_is_standalone_and_read_only():
    script = ssh.render_script(spec())
    compile(script, '<probe>', 'exec')
    assert 'UnitFileState' in script
    assert 'operational_evidence' in script
    assert 'sudo' not in script


@pytest.mark.parametrize('sources', [
    {'service_'+'b'*16: {'intentional_drain': True}},
    {REF: {'command': 'touch /tmp/x'}},
    {REF: {'intentional_drain': 'yes'}},
    {REF: {'admission_unit': 'example.service'}},
    {REF: {'admission_unit': '../bad.service', 'lane': '1'}},
    {REF: {'maintenance_marker': '/a/../b'}},
])
def test_reject_unsafe_sources(sources):
    with pytest.raises(ValueError): spec(sources)


def test_generated_probe_executes_sources_without_remote_io(capsys, monkeypatch):
    value=spec({REF: {'intentional_drain': True, 'admission_unit': 'example-admission.service', 'lane': '1', 'headroom': True}})
    namespace={'__name__': 'probe_test'}
    exec(ssh.render_script(value), namespace)
    calls=[]
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0].endswith('systemctl'):
            return b'LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled'
        message=(json.dumps({'pressure': True, 'held': {'1':123}})
                 if argv[-1]=='example-admission.service' else 'single-job runner: waiting for 32 GiB root headroom')
        return json.dumps({'MESSAGE': message, '__REALTIME_TIMESTAMP':'10000000'}).encode()
    namespace['_bounded_run']=run
    monkeypatch.setenv('SSH_ORIGINAL_COMMAND', 'probe-v1')
    namespace['main']()
    data=ssh._decode(capsys.readouterr().out,value,False)
    rows=data['operational_evidence'][REF]
    assert {r['kind'] for r in rows}=={'drained','disk_pressure','root_headroom'}
    assert all(r['state']=='held' for r in rows)
    assert rows[1]['observed_at']==10
    assert len(calls)==3


def test_spec_copies_nested_source_config():
    sources={REF: {'intentional_drain': True}}
    value=spec(sources)
    sources[REF]['intentional_drain']=False
    assert value.lifecycle_sources[REF]['intentional_drain'] is True


def test_decode_old_response_compatible_and_new_strict():
    value=spec()
    payload=dict(version=1,services={REF:'stopped'},disk_free_bytes=1,load=None,tools={})
    assert ssh._decode(json.dumps(payload),value,False)['services'][REF]=='stopped'
    row=dict(kind='drained',source='systemd',state='held',observed_at=10,recorded_at=None)
    payload['operational_evidence']={REF:[row]}
    assert ssh._decode(json.dumps(payload),value,False)['operational_evidence'][REF]==[row]
    row['raw_log']='private'
    with pytest.raises(ssh.ProbeError): ssh._decode(json.dumps(payload),value,False)


@pytest.mark.parametrize('field', ['observed_at', 'recorded_at'])
@pytest.mark.parametrize('bad', [1e300, 10**400, 253402300800, -1, True, '1000', [], {}, float('inf'), float('nan')])
def test_decode_rejects_unrepresentable_timestamps(field, bad):
    row = dict(kind='drained', source='systemd', state='held', observed_at=10, recorded_at=None)
    row[field] = bad
    payload = dict(version=1, services={REF:'stopped'}, disk_free_bytes=1,
                   load=None, tools={}, operational_evidence={REF:[row]})
    with pytest.raises(ssh.ProbeError):
        ssh._decode(json.dumps(payload), spec(), False)


@pytest.mark.parametrize('field', ['kind', 'source', 'state'])
@pytest.mark.parametrize('bad', [[], {}, True, None])
def test_decode_rejects_malformed_enums(field, bad):
    row = dict(kind='drained', source='systemd', state='held', observed_at=10, recorded_at=None)
    row[field] = bad
    payload = dict(version=1, services={REF:'stopped'}, disk_free_bytes=1,
                   load=None, tools={}, operational_evidence={REF:[row]})
    with pytest.raises(ssh.ProbeError):
        ssh._decode(json.dumps(payload), spec(), False)
