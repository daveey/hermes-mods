"""Authenticated cached API. Importing this module never starts a collector.

Mounted by the dashboard at /api/plugins/runners. The core owns Host validation,
OAuth verification and token verification. No caller-selected profile/home is used.
Cookie sessions require a same-origin Origin for refresh; native loopback session
and registered service tokens can omit Origin, but never send a foreign Origin.
"""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sqlite3
import sys
import threading
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request

_lock = threading.RLock()


def _backend():
    # Dashboard loads this file by path, outside its package.
    with _lock:
        name = 'hermes_runners_backend'
        if name not in sys.modules:
            root = Path(__file__).resolve().parents[1]
            spec = importlib.util.spec_from_file_location(
                name, root / '__init__.py', submodule_search_locations=[str(root)])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules[name] = module
        from hermes_runners_backend.config import ConfigFile
        from hermes_runners_backend.service import Service
        return ConfigFile, Service


def active_profile():
    from hermes_constants import get_hermes_home, profile_name_for_home
    return profile_name_for_home(get_hermes_home())


# One entry per profile home (bounded by the number of profiles; an entry is replaced when its
# file changes and dropped when it fails). A single shared entry made every other profile's poll
# (no runners.yaml -> FileNotFoundError) discard the owner's parsed config, so the owner re-parsed
# and re-validated it (~0.6 s) on each fleet poll. Never cache a Service or a response:
# snapshot owner/revision checks and profile authorization still run per request.
_config_cache = {}


def _signature(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns,
            stat.st_ctime_ns, stat.st_mode, stat.st_uid, stat.st_gid)


_home_locks = {}


def _home_lock(home):
    # Single-flight per profile home. The module-wide _lock is held only to look the lock up, so a
    # profile without runners.yaml (or with a warm cache) never queues behind another profile's parse.
    with _lock:
        return _home_locks.setdefault(home, threading.Lock())


def service(*, detached=True):
    """A Service on this profile's cached, validated config. ``detached=False`` hands it the cached config itself
    (no per-call deep copy): only for callers that never mutate it or anything it returns (see ``_ci_section``)."""
    from hermes_constants import get_hermes_home
    ConfigFile, Service = _backend()
    home = Path(get_hermes_home()).resolve()
    with _home_lock(home):
        try:
            path = home / 'runners.yaml'
            key = (home, active_profile(), _signature(path))
            cached = _config_cache.get(home)
            if cached is None or cached[0] != key:
                # Drop this home's previous owner/revision BEFORE parsing. An invalid,
                # missing or concurrently changed file must never use stale data.
                _config_cache.pop(home, None)
                config = ConfigFile(path).load()
                if _signature(path) != key[2]:
                    raise RuntimeError('configuration_changed')
                # The fingerprint keys the inventory memo (service.py): computed once per parsed config,
                # not marshalled on every request.
                _config_cache[home] = cached = (key, config, Service.config_fingerprint(config))
            # Detach mutable structures so responses/services cannot poison the
            # validated cache, including its owner epoch and config revision.
            config = deepcopy(cached[1]) if detached else cached[1]
        except Exception:
            _config_cache.pop(home, None)
            raise
    return Service(home / 'runners/state.sqlite', config, fingerprint=cached[2])


def _origin(value):
    # Reject URL syntax not legal in an Origin, including urlsplit's stripped controls.
    if not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError('invalid origin')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment or '?' in value or '#' in value):
        raise ValueError('invalid origin')
    return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == 'https' else 80)


def authorize(request: Request):
    try:
        from hermes_cli.web_server import _require_token, _has_valid_session_token
    except ImportError:
        raise HTTPException(503, 'auth_unavailable') from None
    token_authenticated = getattr(request.state, 'token_authenticated', False) is True
    if not token_authenticated:
        _require_token(request)
    # In OAuth-gated mode the loopback token must never override cookie policy.
    native = token_authenticated or (
        not getattr(request.app.state, 'auth_required', False)
        and _has_valid_session_token(request))
    origins = request.headers.getlist('origin')
    if origins:
        try:
            if len(origins) != 1 or _origin(origins[0]) != _origin(str(request.url.replace(path='', query='', fragment=''))):
                raise ValueError('cross origin')
        except ValueError:
            raise HTTPException(403, 'cross_origin') from None
    if request.headers.get('sec-fetch-site') in ('cross-site', 'same-site'):
        raise HTTPException(403, 'cross_origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and not origins and not native:
        raise HTTPException(403, 'origin_required')


router = APIRouter(prefix='/v1', dependencies=[Depends(authorize)])


def invoke(operation, *args, **kwargs):
    try:
        backend = service()
        profile = active_profile()
        if not profile or backend.config['owner_profile'] != profile:
            raise HTTPException(403, 'owner_profile_mismatch')
        return getattr(backend, operation)(*args, **kwargs)
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(404, 'not_found') from None
    except ValueError:
        raise HTTPException(400, 'invalid_request') from None
    except (RuntimeError, OSError, sqlite3.Error, ImportError):
        raise HTTPException(503, 'unavailable') from None


@router.get('/enrollment')
def enrollment(request: Request = None):
    """Profile-local capability discovery; never opens state or starts collection."""
    profile = active_profile()
    try:
        backend = service()
    except FileNotFoundError:
        return dict(contract_version=1, profile=profile, enrolled=False, capabilities=[])
    except (ValueError, RuntimeError, OSError, ImportError):
        raise HTTPException(503, 'configuration_unavailable') from None
    if not profile or backend.config['owner_profile'] != profile:
        raise HTTPException(403, 'owner_profile_mismatch')
    enabled = backend.config['features']['repair_sessions'] and optional_principal(request) is not None
    return dict(contract_version=1, profile=profile, enrolled=True,
                capabilities=['inventory', 'inspect', 'refresh'] + (['repair', 'verify', 'maintenance'] if enabled else []),
                features=dict(repair_sessions=enabled))


@router.get('/inventory')
def inventory():
    return invoke('inventory')


def _fleet():
    # Pure module with no package imports; load it by path like the backend.
    with _lock:
        name = 'hermes_runners_fleet'
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'fleet.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules[name] = module
        return sys.modules[name]


def _waiting_cards(fleet, shadow_dir):
    """Use the machine-wide dispatcher's profile policy, not the polled UI profile."""
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from hermes_cli.kanban_db import kanban_home
    from hermes_cli.kanban_db_dispatch import _profile_exists_fn
    token = set_hermes_home_override(kanban_home())
    try:
        return fleet.waiting_cards(shadow_dir, _profile_exists_fn())
    finally:
        reset_hermes_home_override(token)


def _gateway_section():
    import time
    fleet = _fleet()
    from hermes_cli import kanban_sync_client as ksc, kanban_worker_node as node
    now_ms = int(time.time() * 1000)
    leases = fleet.live_leases(ksc.shadow_path('x').parent, now_ms)
    registry = node.registry()  # core caches it for REGISTRY_TTL_S; a failed read keeps the last value
    read_at = node._REGISTRY.get('at')
    age = time.monotonic() - read_at if registry is not None and read_at is not None else None
    section = fleet.gateway_workers(registry, leases, now_ms, registry_age_s=age,
                                    local_cap=_local_cap(), eligible=node.eligible,
                                    waiting=_waiting_cards(fleet, ksc.shadow_path('x').parent))
    return section


# (path, signature) -> parsed ``kanban.max_in_progress``. The cap is machine-wide, so one entry
# serves every profile route; re-parsing the (large) config with pure-Python YAML cost ~0.15 s of
# GIL-held CPU per fleet build, ten times per poll burst.
_cap_cache = None
_cap_lock = threading.Lock()


def _configured_cap(path):
    import yaml
    try:
        sig = _signature(path)
    except OSError:
        return None
    global _cap_cache
    with _cap_lock:
        if _cap_cache is not None and _cap_cache[0] == (path, sig):
            return _cap_cache[1]
    try:
        raw = (yaml.safe_load(path.read_text()) or {})
        raw = (raw.get('kanban') or {}).get('max_in_progress')
        configured = int(raw) if raw is not None and int(raw) > 0 else None
    except (OSError, yaml.YAMLError, AttributeError, TypeError, ValueError):
        return None  # unreadable/invalid: never cached, retried next build
    if _signature(path) == sig:  # changed while parsing: do not cache
        with _cap_lock:
            _cap_cache = ((path, sig), configured)
    return configured


def _local_cap():
    """This machine's dispatcher cap: ``kanban.max_in_progress`` in the kanban home's config.yaml.

    The dispatcher and the board are machine-wide (kanban_home), not per profile. Reading the
    active profile's config gave the memory-derived default (8 on the hub) to every non-default
    profile route, and the panel showed whichever profile answered last.
    """
    from hermes_cli.kanban_db import kanban_home
    from hermes_cli.kanban_db_dispatch import resolve_max_in_progress
    return resolve_max_in_progress(_configured_cap(kanban_home() / 'config.yaml'))


def _ci_section():
    fleet = _fleet()
    try:
        # Read-only from here on (config, inventory, snapshot): the cached config itself, not a deep copy.
        backend = service(detached=False)
    except FileNotFoundError:
        return dict(state='not_configured', machines=[])
    if backend.config['owner_profile'] != active_profile():
        return dict(state='unavailable', reason='owner_profile_mismatch', machines=[])
    import time
    # Both reads are read-only: the shared memo, not a per-call copy (service.py, card L).
    return fleet.ci_workers(backend.inventory(shared=True), fleet.host_load(backend._snapshot(), time.time()))


FLEET_CACHE_S = 5
# An expired value younger than this is served while a background rebuild runs; older, the poll waits.
FLEET_STALE_MAX_S = 120
# profile -> dict(at, value, lock, gen). Each profile has its own entry and its own single-flight lock, so
# the Desktop's concurrent per-profile polls neither evict each other (a scalar cache keyed by the
# last profile missed on every other profile) nor queue behind one global build lock.
_fleet_cache = {}
_fleet_guard = threading.Lock()  # protects the dict only; never held while building


def _fleet_entry(profile):
    with _fleet_guard:
        return _fleet_cache.setdefault(profile, dict(at=None, value=None, lock=threading.Lock(), gen=0))


# The gateway section is machine-wide (board replicas, registry, kanban-home cap: no profile input),
# so it has ONE entry and one single-flight lock shared by every profile route. Per-profile entries
# rebuilt it ten times per Desktop poll burst (a 15 s poll never hits a 5 s cache), concurrently:
# 65 ms alone became 1.3-2.4 s of GIL convoy for all ten polls at once.
_gateway_cache = dict(at=None, value=None, lock=threading.Lock(), gen=0)


def _fleet_invalidate():
    # ``gen`` drops the result of a rebuild already running: it may have read the state before the change.
    with _fleet_guard:
        for entry in list(_fleet_cache.values()) + [_gateway_cache]:
            entry.update(at=None, value=None, gen=entry.get('gen', 0) + 1)


def _section(build):
    try:
        return build()
    except (RuntimeError, OSError, sqlite3.Error, ImportError, ValueError, KeyError) as exc:
        return dict(state='unavailable', reason=type(exc).__name__, machines=[])


_rebuild_local = threading.local()  # set inside a background rebuild: nested sections build fresh, inline
_rebuilds = set()  # running background rebuild threads (tests join them)
_rebuilds_guard = threading.Lock()


def _store(entry, gen, value):
    import time
    with _fleet_guard:
        if entry.get('gen', 0) == gen:  # not invalidated while building
            entry.update(at=time.monotonic(), value=value)


def _rebuild_in_background(entry, build, gen):
    """Runs with ``entry['lock']`` already held by the poll that started it; releases it when done.

    The build runs in a copy of the poll's context: the profile home is a context-local override
    (hermes_constants), and a bare thread would build (and owner-check) the CI section as the process home.
    """
    import contextvars
    ctx = contextvars.copy_context()

    def run():
        _rebuild_local.active = True
        try:
            _store(entry, gen, build())
        except Exception:  # noqa: BLE001 -- keep serving the previous value; the next poll retries
            import logging
            logging.getLogger(__name__).warning('runners fleet: background rebuild failed', exc_info=True)
        finally:
            entry['lock'].release()
            with _rebuilds_guard:
                _rebuilds.discard(threading.current_thread())
    thread = threading.Thread(target=ctx.run, args=(run,), name='runners-fleet-rebuild', daemon=True)
    with _rebuilds_guard:
        _rebuilds.add(thread)
    try:
        thread.start()
    except BaseException:
        with _rebuilds_guard:
            _rebuilds.discard(thread)
        entry['lock'].release()
        raise


def _join_rebuilds(timeout=None):
    with _rebuilds_guard:
        threads = list(_rebuilds)
    for t in threads:
        t.join(timeout)


def _cached(entry, build):
    """``entry``'s value while younger than FLEET_CACHE_S; else one build at a time (single-flight).

    Stale-while-revalidate: an expired value younger than FLEET_STALE_MAX_S is returned at once and the
    rebuild runs on a background thread (one per entry). The Desktop polls every ~15 s with a 5 s budget, so
    almost every poll is the one that finds the entry expired; building inline put the whole build (a Board
    ``GET /v2/machines`` registry refresh with a 30 s HTTP timeout, CPU stretched by /api/sessions bursts)
    on that poll: hub 2026-11-03 00:01:42Z took 11.2 s with the worker in the registry fetch, and Desktop
    timed out at 5 s (also 00:32:58Z, 02:56:23Z). Concurrent pollers likewise get the previous value instead of
    parking AnyIO workers on the lock (t_01a18b4b710b722e). A cold entry (first poll, after
    ``_fleet_invalidate``) or one past FLEET_STALE_MAX_S still waits, so a create is read back; a rebuild
    that was running across an invalidation does not store its value (``gen``).
    """
    import time
    at, value = entry['at'], entry['value']
    age = None if value is None or at is None else time.monotonic() - at
    if age is not None and age < FLEET_CACHE_S:
        return value
    lock = entry['lock']
    if age is not None and age < FLEET_STALE_MAX_S and not getattr(_rebuild_local, 'active', False):
        if lock.acquire(blocking=False):
            _rebuild_in_background(entry, build, entry.get('gen', 0))
        return value
    lock.acquire()
    try:
        at, value = entry['at'], entry['value']
        if value is not None and at is not None and time.monotonic() - at < FLEET_CACHE_S:
            return value
        gen = entry.get('gen', 0)
        value = build()
        _store(entry, gen, value)
        return value
    finally:
        lock.release()


def _shared_gateway_section():
    return _cached(_gateway_cache, lambda: _section(_gateway_section))


@router.get('/fleet')
def fleet_view():
    """Machines and slots: gateway workers (Board registry + leases) and CI workers (collector).

    One build per FLEET_CACHE_S per profile, single-flight per profile (a poll arriving during a rebuild gets
    the previous value, see ``_cached``); the machine-wide gateway
    section inside it is built once per FLEET_CACHE_S for all profiles (_shared_gateway_section), so
    a poll burst over every profile costs one registry/replica read plus one CI projection each.
    """
    profile = active_profile()
    return _cached(_fleet_entry(profile), lambda: _build_fleet(profile))


def _build_fleet(profile):
    sections = dict(gateways=_shared_gateway_section(), ci=_section(_ci_section))
    from datetime import datetime, timezone
    return dict(contract_version=1, profile=profile,
                generated_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'), **sections)


@router.get('/runners/{runner_id}')
def inspect(runner_id: str, request: Request, history_limit: int = 100):
    return invoke('inspect', runner_id, history_limit, principal=optional_principal(request))


@router.post('/refresh')
def refresh(payload: dict):
    return invoke('refresh', payload)


def repair_principal(request):
    # Match core _transport_auth_user_id exactly. Loopback/service tokens are
    # not login identities, even when they authorize inventory reads.
    session = getattr(request.state, 'session', None)
    provider, user = getattr(session, 'provider', None), getattr(session, 'user_id', None)
    if not all(isinstance(v, str) and v.strip() for v in (provider, user)):
        raise HTTPException(403, 'authenticated_principal_required')
    return f'{provider.strip()}:{user.strip()}'


def optional_principal(request):
    if request is None:
        return None
    try:
        return repair_principal(request)
    except HTTPException:
        return None


def repair_domain(backend):
    _backend()
    from hermes_runners_backend.repairs import domain_for
    return domain_for(backend)


def repair_invoke(request, operation, payload):
    principal = repair_principal(request)
    try:
        backend = service()
        if backend.config['owner_profile'] != active_profile():
            raise HTTPException(403, 'owner_profile_mismatch')
        domain = repair_domain(backend)
        if operation == 'list':
            return dict(meta=backend.inventory()['meta'], data=domain.list(principal=principal))
        elif operation == 'reserve':
            from uuid import uuid4
            from hashlib import sha256
            requester = dict(principal_id='principal_' + sha256(principal.encode()).hexdigest()[:32], connection_id=None,
                             session_id=None, request_id='request_' + uuid4().hex)
            repair = domain.reserve(payload, requester, principal=principal)
            domain.register(principal)
        elif operation == 'reconcile':
            repair = domain.reconcile(payload, principal=principal)
            repair = domain.reconcile_verification(payload, principal=principal)
        elif operation == 'maintenance_revision':
            domain._check(principal)
            return dict(meta=backend.inventory()['meta'], data=dict(revision=domain.maintenance_revision()))
        elif operation == 'verify':
            return domain.verify(payload, principal=principal)
        elif operation == 'maintenance':
            return dict(meta=backend.inventory()['meta'], data=domain.maintenance(payload))
        else:
            raise ValueError('unsupported_operation')
        return dict(meta=backend.inventory()['meta'], data=dict(
            repair=repair, owner_route=None, route_status='unresolved'))
    except HTTPException:
        raise
    except PermissionError:
        raise HTTPException(403, 'repair_access_denied') from None
    except KeyError:
        raise HTTPException(404, 'not_found') from None
    except ValueError:
        raise HTTPException(409, 'invalid_or_conflicting_request') from None
    except (RuntimeError, OSError, sqlite3.Error, ImportError, NotImplementedError):
        raise HTTPException(503, 'repair_runtime_unavailable') from None


@router.get('/repairs')
def repair_list(request: Request):
    return repair_invoke(request, 'list', None)


@router.post('/repairs', status_code=202)
def repair_create(request: Request, payload: dict):
    return repair_invoke(request, 'reserve', payload)


@router.get('/repairs/{repair_id}')
def repair_lookup(request: Request, repair_id: str):
    return repair_invoke(request, 'reconcile', repair_id)


@router.post('/repairs/{repair_id}/verify', status_code=202)
def repair_verify(request: Request, repair_id: str, payload: dict):
    if 'repair_id' in payload:
        raise HTTPException(400, 'path_identity_only')
    return repair_invoke(request, 'verify', dict(payload, repair_id=repair_id))


@router.post('/maintenance')
def maintenance(request: Request, payload: dict):
    return repair_invoke(request, 'maintenance', payload)


@router.get('/maintenance')
def maintenance_version(request: Request):
    # Separate overlay CAS revision; do not mutate owner/config identity and
    # invalidate existing repair associations whenever maintenance changes.
    return repair_invoke(request, 'maintenance_revision', None)


# ---------------------------------------------------------------- dispatch (phase G)

def jobs_domain(backend):
    _backend()
    from hermes_constants import get_hermes_home
    from hermes_runners_backend.jobs import domain_for
    return domain_for(backend, Path(get_hermes_home()).resolve())


def _start_background(fn):
    threading.Thread(target=fn, name='runners-job-reconcile', daemon=True).start()


def _job_error(exc):
    # Matched by contract shape: the module may be loaded under the disk or package name.
    return type(exc).__name__ == 'JobError' and callable(getattr(exc, 'error', None))


def jobs_invoke(operation, *args, request=None, **kwargs):
    try:
        backend = service()
        if backend.config['owner_profile'] != active_profile():
            raise HTTPException(403, 'owner_profile_mismatch')
        domain = jobs_domain(backend)
        if operation == 'submit':
            # Dispatch needs an authenticated login principal; canary mode is never reachable here.
            return domain.submit(*args, principal=repair_principal(request))
        if operation == 'get':
            return domain.get(*args, background=_start_background)
        return domain.workflows()
    except HTTPException:
        raise
    except Exception as exc:
        if _job_error(exc):
            headers = {'Retry-After': str(exc.retry_after)} if exc.retry_after else None
            raise HTTPException(exc.status, exc.error(), headers=headers) from None
        if isinstance(exc, (ValueError, KeyError)):
            raise HTTPException(422, dict(code='invalid_request', retryable=False, reason_code='invalid_request')) from None
        if isinstance(exc, (RuntimeError, OSError, sqlite3.Error, ImportError)):
            raise HTTPException(503, 'unavailable') from None
        raise


@router.get('/workflows')
def workflows():
    return jobs_invoke('workflows')


@router.post('/jobs', status_code=202)
def job_submit(request: Request, payload: dict):
    return jobs_invoke('submit', payload, request=request)


@router.get('/jobs/{job_id}')
def job_lookup(job_id: str):
    return jobs_invoke('get', job_id)
