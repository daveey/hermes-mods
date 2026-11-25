"""Configured source acquisition is separate from owner receipt certification."""
import copy
import json
import os
import sys

import pytest
from sr_helpers import PLUGIN, load

T = load('_sr_target_reader_tests', PLUGIN / 'target_reader.py')
REF = 'owner-target:source-a/desktop'
SHA = 'a'*40
NOW = 1800000000


def observation(kind='update'):
    return {'version':1, 'observed_at':NOW, 'selector':REF, 'disposition':'pending',
            'target':{'kind':kind, 'identity':REF+'@'+'f'*64, 'revision':('entry-42:' if kind=='queue' else '')+SHA}}


def test_real_subprocess_fixed_argv_and_validated_selector(tmp_path):
    script = tmp_path/'source.py'
    script.write_text('import json,sys\nassert sys.argv[1:]==["observe","--selector",'+repr(REF)+']\nprint('+repr(json.dumps(observation()))+')\n')
    config = tmp_path/'target-readers.json'
    config.write_text(json.dumps({'version':1,'authorities':{'source-a':[sys.executable,str(script)]}}))
    got = T.read_target(REF, config, NOW)
    assert got['state']=='owner_target_pending'
    assert got['target_observation']==observation()
    assert T.read_target('owner-target:source-a/desktop;touch',config,NOW)['state']=='target_unavailable'
    assert T.read_target('owner-target:foreign/desktop',config,NOW)['state']=='target_unavailable'


@pytest.mark.parametrize('patch', [
    {'selector':REF+'x'}, {'version':True}, {'observed_at':float('nan')},
    {'observed_at':NOW-1201}, {'observed_at':NOW+31}, {'disposition':'failed'},
    {'disposition':'satisfied'}, {'disposition':'unavailable'},
    {'target':{'kind':'update','identity':'foreign','revision':SHA}},
    {'target':{'kind':'update','identity':REF,'revision':'entry:'+SHA}},
    {'target':{'kind':'queue','identity':REF,'revision':SHA}},
    {'target':{'kind':'update','identity':REF,'revision':'short'}},
])
def test_unavailable_adverse_changed_scope_and_bad_revision_fail_closed(patch):
    assert not T.valid_observation({**observation(),**patch},REF,NOW)


def test_queue_generation_and_acquisition_clock_tolerance():
    value = observation('queue')
    assert T.valid_observation(value,REF,NOW)
    value['observed_at'] = NOW+30
    assert T.valid_observation(value,REF,NOW)
    value['observed_at'] = NOW-1200
    assert T.valid_observation(value,REF,NOW)


@pytest.mark.parametrize('raw', [b'{"version":1,"version":1}',b'{"observed_at":NaN}',b'x'*16385])
def test_json_bound_duplicate_nonfinite(raw):
    with pytest.raises(ValueError):
        T._json(raw)


def test_acquisition_is_bounded_and_refuses_nonzero(tmp_path):
    for code in ('import sys;sys.stdout.write("x"*20000)', 'raise SystemExit(2)'):
        script = tmp_path/'source.py'
        script.write_text(code)
        with pytest.raises(ValueError):
            T._run([sys.executable,str(script)])


def test_timeout_and_fifo_config_do_not_hang(tmp_path,monkeypatch):
    monkeypatch.setattr(T,'TIMEOUT',.05)
    script = tmp_path/'slow.py'
    script.write_text('import time;time.sleep(2)')
    with pytest.raises(ValueError, match='timeout'):
        T._run([sys.executable,str(script)])
    fifo = tmp_path/'config-fifo'
    os.mkfifo(fifo)
    assert T.read_target(REF,fifo,NOW)['state']=='target_unavailable'


def test_scanner_requires_independent_observation_not_receipt_projection():
    scan = load('_sr_target_scanner_tests',PLUGIN/'scan.py')
    awaited = {'kind':'external','ref':REF}
    value = observation()
    live = {'state':'owner_target_pending','happened':False,'target_observation':value}
    assert scan.reassessment_target(awaited,live,NOW)==value['target']
    for patch in ({'happened':True},{'state':'offline'},
                  {'target_observation':{**value,'disposition':'failed'}},
                  {'target_observation':{**value,'selector':REF+'x'}}):
        assert scan.reassessment_target(awaited,{**live,**patch},NOW) is None
    assert scan.reassessment_target(awaited,{'state':{'target':value['target']}},NOW) is None
    assert scan.reassessment_target({**awaited,'gateway':'foreign'},live,NOW) is None
