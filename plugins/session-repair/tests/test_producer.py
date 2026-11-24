import json
import subprocess
import pytest
from sr_helpers import PLUGIN, load
p=load('_sr_producer_test', PLUGIN/'producer.py')

@pytest.mark.parametrize('ref,data,done', [
 ('actions/runs/123',{'status':'completed','conclusion':'success'},True),
 ('actions/runs/123',{'status':'in_progress'},False),
 ('pull/123',{'state':'OPEN','reviewDecision':'APPROVED'},False),
 ('pull/123',{'state':'MERGED'},True),
 ('pull/123',{'state':'CLOSED'},True),
 ('pull/123',{'state':'OPEN','statusCheckRollup':[{'conclusion':'SUCCESS'}]},False),
 ('pull/123',{'state':'OPEN','statusCheckRollup':[]},False),
 ('pull/123',{'state':'OPEN','statusCheckRollup':[{'conclusion':'FAILURE'}]},False),
])
@pytest.mark.parametrize('kind', ['ci', 'deploy', 'external'])
def test_readback(ref,data,done,kind):
    calls=[]
    def runner(argv,**kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv,0,stdout=json.dumps(data))
    out=p.read_github(kind,'https://github.com/example/project/'+ref,runner)
    assert out['happened'] == (done and not (kind == 'deploy' and ref.startswith('pull/')))
    assert calls[0][:4]==['token-broker','exec','--scope','user/github']


def test_bad_refs_not_executed():
    def runner(*a,**kw): raise AssertionError('must not execute')
    assert p.read_github('ci','https://evil.example/pull/123; sh bad',runner)['state']=='not_resolvable'


@pytest.mark.parametrize('kind', ['ci', 'deploy', 'external'])
def test_failure_and_timeout(kind):
    def runner(*a,**kw): raise subprocess.TimeoutExpired('gh',25)
    assert p.read_github(kind,'https://github.com/example/project/pull/123',runner)['state']=='readback_failed'
