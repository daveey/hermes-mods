"""Actual core record keys/profile paths; tools cannot accept payload principals."""
import json
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
import pytest
from plugins.runners import register, tool_principal


@pytest.fixture
def runtime(monkeypatch):
    module = ModuleType('tui_gateway')
    server = SimpleNamespace(_sessions_lock=threading.RLock(),
        _profile_home=lambda profile:Path('/fictional')/profile,
        _session_auth_user_id=lambda record:record.get('auth_user_id'),
        _sessions={'runtime_a':dict(session_key='stored_a', profile_home='/fictional/example',
                                  auth_user_id='oidc:example', agent=SimpleNamespace(session_id='compressed_a'))})
    module.server=server
    monkeypatch.setitem(sys.modules,'tui_gateway',module)
    return server


def test_runtime_stored_compressed_identity_and_profile(runtime):
    for sid in ('runtime_a','stored_a','compressed_a'):
        assert tool_principal(sid,'example')=='oidc:example'
    with pytest.raises((PermissionError,NotImplementedError)):
        tool_principal('stored_a','other')
    runtime._sessions['other']=dict(session_key='stored_a',profile_home='/fictional/other',auth_user_id='oidc:other')
    assert tool_principal('stored_a','example')=='oidc:example'
    runtime._sessions['runtime_a']['auth_user_id']=None
    with pytest.raises(PermissionError):
        tool_principal('runtime_a','example')


def test_tool_dispatch_passes_domain_identity_and_fails_closed(runtime, monkeypatch):
    from plugins.runners.dashboard import plugin_api as api
    calls=[]
    backend=SimpleNamespace(config={'owner_profile':'example'}, inventory=lambda:{'meta':{}})
    domain=SimpleNamespace(reserve=lambda args, requester, **kw: calls.append((args,kw)) or {'id':'repair'},
        start=lambda rid, **kw:{'id':rid}, verify=lambda args, **kw:calls.append((args,kw)) or {'data':{}})
    monkeypatch.setattr(api,'service',lambda:backend)
    monkeypatch.setattr(api,'active_profile',lambda:'example')
    monkeypatch.setattr(api,'repair_domain',lambda _:domain)
    tools={}
    register(SimpleNamespace(register_tool=lambda **kw:tools.update({kw['name']:kw['handler']})))
    args={'principal':'forged'}  # only real domain schema may accept input; not used as authority
    tools['runners_repair'](args,session_id='stored_a')
    assert calls[-1][1] == {'principal':'oidc:example'}
    before=len(calls)
    result=json.loads(tools['runners_repair'](args))
    assert result['error']=='owning_gateway_runtime_required'
    assert len(calls)==before
