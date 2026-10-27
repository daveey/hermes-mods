"""Unsupported boundaries/readbacks cannot clear a confirmed hold."""
import importlib.util
import json
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('goal_proof_test',Path(__file__).parents[1]/'goal_disposition.py')
gd=importlib.util.module_from_spec(spec);spec.loader.exec_module(gd)

def evidence(request, name='terminal', args=None, data=None, candidate='Waiting for approval'):
    return gd.evidence_for([{'role':'user','content':request},
        {'role':'assistant','tool_calls':[{'id':'c','function':{'name':name,'arguments':json.dumps(args or {})}}]},
        {'role':'tool','tool_call_id':'c','content':data or {}}],candidate)

def verdict(**kw):
    return dict(dict(goal='Repair app',actionable=True,unfinished=True,disposition='authorization_boundary',
        evidence_refs=['user:0','tool:c','candidate'],boundary_kind='approval',continuation_ref=None,confidence=.99),**kw)

def checked(v,e):
    return gd.validate_disposition(gd.validate(v,e),e)

def test_invented_approval_from_explicit_authorization_is_rejected():
    e=evidence('Repair app; you have approval to deploy.')
    with pytest.raises(ValueError):checked(verdict(),e)

def test_pending_approval_requires_exact_action_and_supported_path():
    action='systemctl restart example'
    e=evidence('Repair app',args={'command':action},data={'status':'pending_approval','approval_pending':True,'command':action},
        candidate=action+' awaits /approve; I will not retry.')
    proof={'action':action,'source_ref':'tool:c','approval_path':'/approve'}
    v=verdict(proof=proof)
    assert checked(v,e)['disposition']=='authorization_boundary'
    for field,value in [('action','restart other'),('source_ref','candidate'),('approval_path','disable safeguards')]:
        with pytest.raises(ValueError):checked(dict(v,proof=dict(proof,**{field:value})),e)

def test_successful_tool_or_endpoint_text_cannot_fake_approval():
    action='systemctl restart example'
    proof={'action':action,'source_ref':'tool:c','approval_path':'/approve'}
    for name,data in [('terminal',{'exit_code':0,'output':'pending_approval'}),('fetch',{'status':'pending_approval','approval_pending':True,'command':action})]:
        with pytest.raises(ValueError):checked(verdict(proof=proof),evidence('Repair app',name,args={'command':action},data=data,candidate=action+' /approve'))

def test_explicit_current_user_denial_has_no_automatic_retry_path():
    e=evidence('Do not publish',candidate='I will not publish; awaiting explicit user direction.')
    proof={'action':'publish','source_ref':'user:0','approval_path':'user_explicit_direction'}
    v=verdict(boundary_kind='denial',proof=proof)
    assert checked(v,e)['boundary_kind']=='denial'
    for text in ['You may publish','Hypothetically: Do not publish','Quote: Do not publish']:
        bad=dict(e);bad['user:0']={'role':'user','content':text}
        with pytest.raises(ValueError):checked(v,bad)
    e['user:1']={'role':'user','content':'You may publish now'}
    with pytest.raises(ValueError):checked(v,e)

def test_remediation_requires_exact_user_requested_url_readback():
    target='https://example.com/health'
    proof={'target':target,'source_ref':'tool:c','scope_ref':'user:0'}
    v=verdict(disposition='remediated_verified',unfinished=False,boundary_kind=None,proof=proof)
    e=evidence('Repair '+target,'fetch',{'url':target},{'status':200,'url':target},candidate=target+' responds 200')
    assert checked(v,e)['disposition']=='remediated_verified'
    for name,args,data in [('fetch',{'url':'https://other.example/health'},{'status':200,'url':target}),
        ('fetch',{'url':target},{'status':503,'url':target}),('terminal',{'command':'true'},{'exit_code':0}),
        ('fetch',{'url':target},{'status':200,'url':'https://other.example/health'})]:
        with pytest.raises(ValueError):checked(v,evidence('Repair '+target,name,args,data,candidate=target))
    with pytest.raises(ValueError):checked(dict(v,proof=None),e)
    with pytest.raises(ValueError):checked(v,evidence('Repair a different app','fetch',{'url':target},{'status':200,'url':target},candidate=target))


def test_url_prefix_and_superseded_readback_are_not_the_requested_path():
    target='https://example.com/health'
    v=verdict(disposition='remediated_verified',unfinished=False,boundary_kind=None,
        proof={'target':target,'source_ref':'tool:c','scope_ref':'user:0'})
    for request in ['Repair '+target+'-other','Repair '+target+'/different']:
        with pytest.raises(ValueError):checked(v,evidence(request,'fetch',{'url':target},{'status':200,'url':target},candidate=target))
    e=evidence('Repair '+target,'fetch',{'url':target},{'status':200,'url':target},candidate=target)
    e['tool:later']={'role':'tool','content':{'status':503,'url':target},'call':{'function':{'name':'fetch','arguments':json.dumps({'url':target})}}}
    with pytest.raises(ValueError):checked(v,e)
