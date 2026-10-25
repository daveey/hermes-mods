from dataclasses import replace
import pytest


def api():
    from plugins.runners.health import State, Sample, Checks, Policy, advance, project, fleet, capacity
    return State, Sample, Checks, Policy, advance, project, fleet, capacity


def online(**kwargs):
    S, O, C, P, advance, project, *_ = api()
    state = advance(S(), O('one', 100, 'online', 'idle'), now=100)
    return project(state, now=100, **kwargs)


def test_first_and_confirmed_offline_rule1():
    S,O,C,P,a,p,*_ = api()
    s=a(S(),O('a',100,'offline'),now=100)
    assert p(s,now=100).health=='suspected_offline'
    s=a(s,O('b',129,'offline'),now=129)
    assert p(s,now=129).health=='suspected_offline'
    s=a(s,O('c',130,'offline'),now=130)
    assert p(s,now=130).health=='offline' and s.incident_open


def test_two_healthy_cycles_close_incident_recovery_rule1():
    S,O,C,P,a,p,*_ = api()
    s=a(a(S(),O('a',100,'offline'),now=100),O('b',130,'offline'),now=130)
    s=a(s,O('c',160,'online','idle'),now=160)
    assert p(s,now=160).health=='online_idle' and s.incident_open
    assert a(s,O('c',160,'online','idle'),now=160)==s
    s=a(s,O('d',190,'online','idle'),now=190)
    assert not s.incident_open


def test_offline_requires_consecutive_fresh_evidence():
    S,O,C,P,a,p,*_ = api()
    s=a(S(),O('a',100,'offline'),now=100)
    s=a(s,O('b',300,'offline'),now=300)
    assert not s.incident_open
    assert a(s,O('old',90,'offline'),now=300)==s


def test_ready_requires_independent_checks_rule2():
    S,O,C,*_=api()
    assert online().health=='online_idle'
    assert online(checks=C(at=100,required=('disk',),passed=('disk',))).ready
    assert not online(checks=C(at=100,required=('disk','xcode'),passed=('disk',))).ready


def test_busy_not_failure_or_stuck_rule3_and9():
    S,O,C,P,a,p,*_=api()
    s=a(S(),O('a',100,'online','busy'),now=100)
    out=p(s,now=100,last_build_failed=True,job_elapsed_seconds=999999)
    assert out.health=='busy' and not out.ready and 'stuck_job' not in out.reasons
    assert 'stuck_job' in p(s,now=100,job_timeout_evidence=True).reasons


def test_ssh_failure_is_warning_not_offline_rule4():
    *_,=api()
    out=online(management='unreachable',host_at=100)
    assert out.registration=='online' and 'management_unreachable' in out.reasons


@pytest.mark.parametrize('check',['disk','toolchain','service'])
def test_required_check_failure_rule5(check):
    S,O,C,*_=api()
    out=online(checks=C(at=100,required=(check,),failed=(check,)))
    assert out.health=='unavailable' and out.registration=='online'
    assert 'failed:'+check in out.reasons


def test_inventory_stale_after_120_rule6():
    S,O,C,P,a,p,*_=api()
    s=a(S(),O('a',100,'online','idle'),now=100)
    checks=C(at=100,required=('disk',),passed=('disk',))
    assert p(s,now=220,checks=checks).ready
    out=p(s,now=221,checks=checks)
    assert out.health=='stale' and out.registration=='online' and not out.ready


def test_host_stale_after_180_rule6():
    S,O,C,P,a,p,*_=api()
    s=a(S(),O('a',280,'online','idle'),now=280)
    checks=C(at=100,required=('disk',),passed=('disk',))
    assert p(s,now=280,checks=checks,host_at=100).ready
    out=p(s,now=281,checks=checks,host_at=100)
    assert not out.ready and 'host_stale' in out.reasons


@pytest.mark.parametrize('error',['401','403','429','network','timeout'])
def test_source_errors_never_mark_offline_rule7(error):
    S,O,C,P,a,p,f,_=api()
    s=a(S(),O('a',100,'online','idle'),now=100)
    s=a(s,O('b',130,'offline',error=error),now=130)
    out=p(s,now=130)
    assert out.registration=='online' and not s.incident_open
    assert f([out],coverage='complete',source_errors=(error,))['all_healthy'] is False


def test_partial_inventory_never_all_green():
    *_,f,c=api()
    assert not f([online()],coverage='partial')['all_healthy']
    assert not f([],coverage='complete')['all_healthy']


def test_missing_requires_two_complete_enumerations():
    S,O,C,P,a,p,*_=api()
    s=a(S(),O('a',100,'online'),now=100)
    s=a(s,O('b',130,None,complete=False),now=130)
    assert s.registration=='online'
    s=a(s,O('c',160,None),now=160)
    assert s.registration=='online'
    s=a(s,O('d',190,None),now=190)
    assert s.registration=='missing' and s.retired_at==190
    assert p(s,now=190).health=='missing'


@pytest.mark.parametrize('lifecycle',['jit','ephemeral'])
def test_expected_expiry_is_retirement_not_incident(lifecycle):
    S,O,C,P,a,p,*_=api()
    s=S(lifecycle=lifecycle,expected_expiry=150)
    s=a(s,O('a',160,None),now=160)
    assert s.lifecycle=='retired' and not s.incident_open
    assert p(s,now=160).health=='retired'


def test_maintenance_overlay_does_not_mask_stale_rule8():
    S,O,C,P,a,p,*_=api()
    s=a(S(),O('a',100,'online','idle'),now=100)
    out=p(s,now=221,maintenance_until=300)
    assert out.maintenance and out.health=='stale' and not out.ready
    assert not p(s,now=300,maintenance_until=300).maintenance


@pytest.mark.parametrize('free,expected',[(4,'fail'),(5,'warn'),(19,'warn'),(20,'pass')])
def test_disk_policy(free,expected):
    from plugins.runners.health import disk_state
    assert disk_state(free)==expected
    assert disk_state(20,warn_gib=40,fail_gib=25)=='fail'


def test_oversubscription_and_idle_sibling_not_free_capacity():
    *_,c=api()
    assert c(services=2,busy=1,declared=1,enforced=False)=={'possible_oversubscription':True,'available_slots':None}
    assert c(services=2,busy=1,declared=1,enforced=True)['available_slots']==0


def test_pure_projection_does_not_mutate_or_renew():
    S,O,C,P,a,p,*_=api()
    s=a(S(),O('a',100,'online','idle'),now=100)
    before=replace(s)
    assert p(s,now=100)==p(s,now=100)
    p(s,now=999)
    assert s==before and s.at==100


def test_new_offline_streak_not_confirmed_by_old_incident():
    S,O,C,P,a,p,*_=api()
    s=a(a(S(),O('a',100,'offline'),now=100),O('b',130,'offline'),now=130)
    s=a(s,O('c',160,'online','idle'),now=160)
    s=a(s,O('d',190,'offline'),now=190)
    assert s.incident_open and p(s,now=190).health=='suspected_offline'


def test_recovery_cycles_must_be_fresh_and_consecutive():
    S,O,C,P,a,p,*_=api()
    s=a(a(S(),O('a',100,'offline'),now=100),O('b',130,'offline'),now=130)
    s=a(s,O('c',160,'online','idle'),now=160)
    s=a(s,O('d',400,'online','idle'),now=400)
    assert s.incident_open


def test_thresholds_injected():
    S,O,C,P,a,p,*_=api()
    s=a(S(),O('a',100,'online','idle'),now=100)
    assert p(s,now=111,policy=P(inventory_ttl=10)).health=='stale'
