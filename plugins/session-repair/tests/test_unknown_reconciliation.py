import json
from types import SimpleNamespace
import pytest
from test_nudge import setup, nudge, run, finding


def inputs(status='ready', at=11000, event_id=12, kind='condition_met', result=True):
    f = finding(); f['evidence']['park_id'] = 10
    events = [{'id':event_id, 'at':at, 'kind':kind,
               'payload':{'external_result':{'conclusion':'failed'}} if result else {}}]
    src = SimpleNamespace(task=lambda *a:(SimpleNamespace(status=status), object()))
    scan = SimpleNamespace(_events=lambda *a,**kw:events)
    return f, src, scan, events


def test_independent_dispatcher_wake_resolves_frozen_identity():
    f,src,scan,_ = inputs()
    h={'pending':{'at':10000,'text':'frozen','finding':f}}
    a=nudge.settle_unknown(scan,src,f,h)
    assert a['outcome']=='superseded_by_dispatcher'
    assert h['superseded_delivery']['text']=='frozen' and 'pending' not in h
    assert a['independent_wake']['event_id']==12


@pytest.mark.parametrize('kw', [dict(status='scheduled'), dict(status='blocked'),
    dict(at=9999), dict(event_id=9), dict(kind='wake_check_alive'), dict(result=False)])
def test_ambiguous_or_old_receipt_remains_unknown(kw):
    f,src,scan,_=inputs(**kw)
    h={'pending':{'at':10000,'text':'frozen'}}
    assert nudge.settle_unknown(scan,src,f,h)['outcome']=='delivery_unknown'
    assert h['pending']['text']=='frozen'


def test_repark_does_not_resolve_old_unknown():
    f,src,scan,events=inputs()
    events.insert(0, {'id':13,'at':12000,'kind':'scheduled','payload':{}})
    assert nudge.independent_wake(scan,src,f,{'at':10000}) is None


@pytest.mark.parametrize('recover', [False, True])
def test_tick_unknown_pending_until_positive_independent_readback(tmp_path,monkeypatch,recover):
    f,tick,calls,sd=setup(tmp_path,monkeypatch,'live')
    f['evidence']['park_id']=10
    monkeypatch.setattr(nudge,'receipt',lambda *a:None)
    monkeypatch.setattr(nudge,'independent_wake',lambda *a:None if recover else {'event_id':12})
    monkeypatch.setattr(run,'load_nudge',lambda:nudge)
    monkeypatch.setattr(run,'load_scan',lambda:SimpleNamespace(Sources=lambda:None))
    monkeypatch.setattr(run,'hermes_argv',lambda:['hermes'])
    def actual(now):
        return run.tick(home=tmp_path,scanner=lambda:{'findings':[f]},runner=lambda *a,**k:None,now=now,run_mode='shadow')
    first=actual(10000)
    assert not first.get('failed')
    assert bool(first['pending_delivery']) == recover
    if recover:
        monkeypatch.setattr(nudge,'independent_wake',lambda *a:{'event_id':12})
        second=actual(11000)
        assert not second.get('failed')
        assert not second['pending_delivery']
        assert second['nudges'][0]['outcome']=='superseded_by_dispatcher'
    state=json.loads((sd/'nudges-live.json').read_text())
    assert 'pending' not in state['fps'][f['fingerprint']]
    assert state['fps'][f['fingerprint']]['superseded_delivery']['at']==10000
