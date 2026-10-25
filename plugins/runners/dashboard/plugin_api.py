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


def invoke(operation, *args):
    try:
        backend = service()
        profile = active_profile()
        if not profile or backend.config['owner_profile'] != profile:
            raise HTTPException(403, 'owner_profile_mismatch')
        return getattr(backend, operation)(*args)
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(404, 'not_found') from None
    except ValueError:
        raise HTTPException(400, 'invalid_request') from None
    except (RuntimeError, OSError, sqlite3.Error, ImportError):
        raise HTTPException(503, 'unavailable') from None


@router.get('/inventory')
def inventory():
    return invoke('inventory')


@router.get('/runners/{runner_id}')
def inspect(runner_id: str, history_limit: int = 100):
    return invoke('inspect', runner_id, history_limit)


@router.post('/refresh')
def refresh(payload: dict):
    return invoke('refresh', payload)
