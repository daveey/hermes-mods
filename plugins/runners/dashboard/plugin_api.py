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


# One entry, not a growing per-profile cache. Never cache a Service or a response:
# snapshot owner/revision checks and profile authorization still run per request.
_config_cache = None


def _signature(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns,
            stat.st_ctime_ns, stat.st_mode, stat.st_uid, stat.st_gid)


def service():
    from hermes_constants import get_hermes_home
    global _config_cache
    ConfigFile, Service = _backend()
    with _lock:
        try:
            home = Path(get_hermes_home()).resolve()
            path = home / 'runners.yaml'
            key = (home, active_profile(), _signature(path))
            if _config_cache is None or _config_cache[0] != key:
                # Drop the previous owner/revision BEFORE parsing. An invalid,
                # missing or concurrently changed file must never use stale data.
                _config_cache = None
                config = ConfigFile(path).load()
                if _signature(path) != key[2]:
                    raise RuntimeError('configuration_changed')
                _config_cache = (key, config)
            # Detach mutable structures so responses/services cannot poison the
            # validated cache, including its owner epoch and config revision.
            config = deepcopy(_config_cache[1])
        except Exception:
            _config_cache = None
            raise
    return Service(home / 'runners/state.sqlite', config)


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


def _gateway_section():
    import time
    fleet = _fleet()
    from hermes_cli import kanban_sync_client as ksc, kanban_worker_node as node
    from hermes_cli.kanban_db_dispatch import configured_max_in_progress, resolve_max_in_progress
    now_ms = int(time.time() * 1000)
    leases = fleet.live_leases(ksc.shadow_path('x').parent, now_ms)
    registry = node.registry()  # core caches it for REGISTRY_TTL_S; a failed read keeps the last value
    read_at = node._REGISTRY.get('at')
    age = time.monotonic() - read_at if registry is not None and read_at is not None else None
    return fleet.gateway_workers(registry, leases, now_ms, registry_age_s=age,
                                 local_cap=resolve_max_in_progress(configured_max_in_progress()))


def _ci_section():
    fleet = _fleet()
    try:
        backend = service()
    except FileNotFoundError:
        return dict(state='not_configured', machines=[])
    if backend.config['owner_profile'] != active_profile():
        return dict(state='unavailable', reason='owner_profile_mismatch', machines=[])
    return fleet.ci_workers(backend.inventory())


FLEET_CACHE_S = 5
_fleet_cache = dict(at=None, profile=None, value=None)
_fleet_lock = threading.Lock()


@router.get('/fleet')
def fleet_view():
    """Machines and slots: gateway workers (Board registry + leases) and CI workers (collector).

    One build per FLEET_CACHE_S per profile, single-flight: any number of open panels polling
    costs one registry read, a few indexed SQLite reads and one inventory projection.
    """
    import time
    profile = active_profile()
    with _fleet_lock:
        c = _fleet_cache
        if c['value'] is not None and c['profile'] == profile and time.monotonic() - c['at'] < FLEET_CACHE_S:
            return c['value']
        value = _build_fleet(profile)
        c.update(at=time.monotonic(), profile=profile, value=value)
        return value


def _build_fleet(profile):
    sections = {}
    for key, build in (('gateways', _gateway_section), ('ci', _ci_section)):
        try:
            sections[key] = build()
        except (RuntimeError, OSError, sqlite3.Error, ImportError, ValueError, KeyError) as exc:
            sections[key] = dict(state='unavailable', reason=type(exc).__name__, machines=[])
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
