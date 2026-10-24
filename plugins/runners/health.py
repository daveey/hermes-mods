"""Pure collector reducers and display projections. Times are injected UTC epoch seconds.

Sample IDs identify collection cycles, not render requests. Adapters must pass complete
only for successful pagination with total-count parity. Checks are independently
verified observations, never declarative config fields. Persist State between cycles.
"""
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Policy:
    inventory_ttl: float = 120
    host_ttl: float = 180
    offline_interval: float = 30


@dataclass(frozen=True)
class Sample:
    cycle: str
    at: float
    registration: str | None
    activity: str = 'unknown'
    complete: bool = True
    error: str | None = None


@dataclass(frozen=True)
class State:
    registration: str = 'unknown'
    activity: str = 'unknown'
    at: float | None = None
    cycle: str | None = None
    processed_at: float | None = None
    offline_since: float | None = None
    offline_count: int = 0
    healthy_cycles: int = 0
    absent_cycles: int = 0
    incident_open: bool = False
    lifecycle: str = 'persistent'
    expected_expiry: float | None = None
    retired_at: float | None = None
    source_error: str | None = None


@dataclass(frozen=True)
class Checks:
    at: float | None = None
    required: tuple[str, ...] = ()
    passed: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    warned: tuple[str, ...] = ()


@dataclass(frozen=True)
class Projection:
    health: str
    registration: str
    activity: str
    ready: bool
    freshness: str
    maintenance: bool
    incident_open: bool
    reasons: tuple[str, ...]


def advance(state: State, sample: Sample, *, now: float, policy=Policy(), healthy=True):
    """Reduce a new cycle; errors retain observations and break confirmation streaks.

    healthy must reflect independently checked cycle health for incident resolution.
    Missing registrations are tombstones until Store.retire_runner/prune archives them.
    """
    if sample.registration not in (None, 'online', 'offline', 'unknown'):
        raise ValueError('invalid registration')
    if sample.activity not in ('idle', 'busy', 'unknown'):
        raise ValueError('invalid activity')
    if (sample.cycle == state.cycle or sample.at > now or
            now - sample.at > policy.inventory_ttl or
            (state.processed_at is not None and sample.at <= state.processed_at)):
        return state
    base = replace(state, cycle=sample.cycle, processed_at=sample.at)
    if sample.error or not sample.complete:
        return replace(base, source_error=sample.error or 'partial', offline_since=None,
                       offline_count=0, healthy_cycles=0, absent_cycles=0)
    base = replace(base, source_error=None)
    if sample.registration is None:
        if state.lifecycle in ('ephemeral', 'jit') and state.expected_expiry is not None and sample.at >= state.expected_expiry:
            return replace(base, lifecycle='retired', retired_at=state.retired_at or sample.at,
                           incident_open=False, healthy_cycles=0, offline_count=0, offline_since=None)
        missing = state.absent_cycles + 1
        return replace(base, absent_cycles=missing, healthy_cycles=0, offline_count=0,
                       offline_since=None, registration='missing' if missing >= 2 else state.registration,
                       incident_open=state.incident_open or missing >= 2,
                       retired_at=(state.retired_at or sample.at) if missing >= 2 else state.retired_at)
    base = replace(base, at=sample.at, registration=sample.registration, activity=sample.activity,
                   absent_cycles=0, retired_at=None)
    if sample.registration == 'offline':
        consecutive = state.registration == 'offline' and state.at is not None and sample.at-state.at <= policy.inventory_ttl and state.offline_since is not None
        since = state.offline_since if consecutive else sample.at
        count = state.offline_count+1 if consecutive else 1
        confirmed = count >= 2 and sample.at-since >= policy.offline_interval
        return replace(base, offline_since=since, offline_count=count, healthy_cycles=0,
                       incident_open=state.incident_open or confirmed)
    previous_cycles = state.healthy_cycles if state.at is not None and sample.at-state.at <= policy.inventory_ttl else 0
    cycles = previous_cycles+1 if sample.registration == 'online' and healthy else 0
    return replace(base, offline_count=0, offline_since=None, healthy_cycles=cycles,
                   incident_open=state.incident_open and cycles < 2)


def project(state: State, *, now: float, checks=Checks(), policy=Policy(),
            management='unknown', host_at=None, maintenance_until=None,
            last_build_failed=False, job_elapsed_seconds=None, job_timeout_evidence=False):
    """No elapsed-job heuristic; build conclusion is deliberately metadata only."""
    reasons = []
    fresh = state.at is not None and 0 <= now-state.at <= policy.inventory_ttl
    checks_fresh = checks.at is not None and 0 <= now-checks.at <= policy.host_ttl
    host_fresh = host_at is not None and 0 <= now-host_at <= policy.host_ttl
    maintenance = maintenance_until is not None and maintenance_until > now
    required = set(checks.required)
    failed = required.intersection(checks.failed) if checks_fresh else set()
    warned = required.intersection(checks.warned) if checks_fresh else set()
    verified = bool(required) and checks_fresh and required <= set(checks.passed) and not failed and not warned
    if host_at is not None and not host_fresh:
        reasons.append('host_stale')
        verified = False
    if management == 'unreachable' and host_fresh:
        reasons.append('management_unreachable')
    reasons.extend('failed:'+x for x in sorted(failed))
    reasons.extend('warn:'+x for x in sorted(warned))
    if state.source_error:
        reasons.append('source_unavailable')
        verified = False
    if job_timeout_evidence:
        reasons.append('stuck_job')
    if state.lifecycle == 'retired':
        health = 'retired'
    elif state.registration == 'missing':
        health = 'missing'
    elif not fresh:
        health = 'stale' if state.at is not None else 'unknown'
    elif state.registration == 'offline':
        confirmed = state.offline_count >= 2 and state.offline_since is not None and state.at-state.offline_since >= policy.offline_interval
        health = 'offline' if confirmed else 'suspected_offline'
    elif state.registration != 'online':
        health = 'unknown'
    elif failed:
        health = 'unavailable'
    elif warned:
        health = 'degraded'
    elif state.activity == 'busy':
        health = 'busy'
    elif state.activity == 'idle':
        health = 'ready' if verified else 'online_idle'
    else:
        health = 'online_unknown_activity'
    if state.registration == 'online' and not verified:
        reasons.append('readiness_unverified')
    return Projection(health, state.registration, state.activity,
                      health == 'ready' and not maintenance, 'fresh' if fresh else ('stale' if state.at is not None else 'unknown'),
                      maintenance, state.incident_open, tuple(reasons))


def disk_state(free_gib, *, warn_gib=20, fail_gib=5):
    if fail_gib < 0 or warn_gib < fail_gib or free_gib < 0:
        raise ValueError('invalid disk thresholds or observation')
    return 'fail' if free_gib < fail_gib else 'warn' if free_gib < warn_gib else 'pass'


def capacity(*, services, busy, declared=None, enforced=False):
    """Advisory potential oversubscription, not a scheduling admission decision."""
    return {'possible_oversubscription': declared is not None and services > declared and not enforced,
            'available_slots': max(0, declared-busy) if enforced and declared is not None else None}


def fleet(projections, *, coverage, source_errors=()):
    rows = tuple(projections)
    return {'all_healthy': bool(rows) and coverage == 'complete' and not source_errors and
            all(r.freshness == 'fresh' and r.health in ('ready', 'busy') and not r.reasons for r in rows),
            'verified_ready': sum(r.ready for r in rows),
            'online': sum(r.registration == 'online' for r in rows),
            'busy': sum(r.activity == 'busy' for r in rows)}
