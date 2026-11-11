"""GET /api/plugins/account-usage/last -> {provider: {entry_id, label, at}} (no secrets)."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter()


def _path() -> str:
    try:
        from hermes_constants import get_hermes_home
        return os.path.join(str(get_hermes_home()), "account_usage_last.json")
    except Exception:
        return os.path.expanduser("~/.hermes/account_usage_last.json")


@router.get("/last")
async def last() -> Dict[str, Any]:
    try:
        with open(_path()) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# A single module identity is shared by REST and future execution integration.
# Importing defines primitives only; it installs no client factory or middleware.
import importlib.util
from pathlib import Path
import sys
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt


def load_selection_module():
    name = 'account_usage_selection'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'selection.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


_selection = load_selection_module()


class SelectionBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    session_id: str = Field(min_length=1, max_length=256)
    provider: str = Field(min_length=1, max_length=64)
    account_id: str = Field(min_length=1, max_length=512)
    expected_revision: StrictInt = Field(ge=0)


def selection_service():
    # Core plugin mounting binds _plugin_route_secret_scope (?profile=).
    # No body/query filesystem path, durable session id, or credential id is trusted.
    from hermes_constants import get_hermes_home
    return _selection.SelectionService(get_hermes_home())


def _call(service, method, *args, **kwargs):
    try:
        return getattr(service, method)(*args, **kwargs)
    except _selection.SelectionConflict as exc:
        raise HTTPException(409, detail=str(exc)) from None
    except _selection.SelectionRejected as exc:
        raise HTTPException(400, detail=str(exc)) from None
    except Exception:
        # Never echo raw loader/SQL/credential exceptions through HTTP.
        raise HTTPException(503, detail='Account selection is unavailable; reconnect the chat or retry') from None


@router.get('/selection')
def get_selection(session_id: str = Query(min_length=1, max_length=256),
                  service=Depends(selection_service)):
    return _call(service, 'get', session_id)


@router.put('/selection')
def put_selection(body: SelectionBody, service=Depends(selection_service)):
    return _call(service, 'put', body.session_id, provider=body.provider,
                 account_id=body.account_id, expected_revision=body.expected_revision)


@router.delete('/selection')
def delete_selection(session_id: str = Query(min_length=1, max_length=256),
                     expected_revision: int = Query(ge=0), service=Depends(selection_service)):
    return _call(service, 'delete', session_id, expected_revision=expected_revision)


# ── Reconnect: renew a disconnected account's OAuth grant in place ─────────────

RECONNECT_PROVIDERS = frozenset({'anthropic', 'openai-codex'})


class ReconnectBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    provider: str = Field(min_length=1, max_length=64)
    account_id: str = Field(min_length=1, max_length=512)


def _pool_tools():
    """(load_pool, identity_for, STATUS_DEAD) from core; isolated so tests can stub it."""
    from agent.account_usage_cache import _identity_id_for
    from agent.credential_pool import STATUS_DEAD, load_pool
    return load_pool, _identity_id_for, STATUS_DEAD


def reconnect_account(provider: str, account_id: str, tools=None) -> Dict[str, Any]:
    """Force-refresh every pooled OAuth credential of one account (the wire ``accounts[].id``).

    Same effect as ``hermes auth refresh <provider> <entry>``: a live refresh token mints new
    tokens and clears the entry's local block. A dead grant can't be renewed here, so the reply
    says ``needs_sign_in`` and the panel sends the user to the sign-in flow. Never returns tokens,
    raw errors or entry secrets."""
    if provider not in RECONNECT_PROVIDERS:
        raise HTTPException(400, detail=f'Reconnect is not supported for {provider}')
    try:
        load_pool, identity_for, status_dead = tools or _pool_tools()
        pool = load_pool(provider)
        matches = [e for e in pool.entries() if identity_for(provider, e) == account_id]
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, detail='Credential pool is unavailable; retry') from None
    if not matches:
        raise HTTPException(404, detail='Account not found; refresh the panel')
    renewed = 0
    for entry in matches:
        if getattr(entry, 'auth_type', None) != 'oauth' or not getattr(entry, 'refresh_token', None):
            continue
        try:
            refreshed = pool.try_refresh_matching(credential_id=entry.id)
        except Exception:
            refreshed = None
        if refreshed is not None and getattr(refreshed, 'last_status', None) != status_dead:
            renewed += 1
    sign_in = f'hermes auth add {provider} --type oauth'
    label = str(getattr(matches[0], 'label', '') or '')
    if label:
        import shlex
        sign_in += f' --label {shlex.quote(label)}'
    return {'ok': renewed > 0, 'renewed': renewed, 'needs_sign_in': renewed == 0,
            'sign_in_command': sign_in}


@router.post('/reconnect')
def post_reconnect(body: ReconnectBody):
    return reconnect_account(body.provider, body.account_id)


# ── Disable: keep an account out of use without removing it (``hermes auth disable``) ──

DISABLE_PROVIDERS = frozenset({'anthropic', 'openai-codex', 'xai-oauth'})


class DisableBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    provider: str = Field(min_length=1, max_length=64)
    account_id: str = Field(min_length=1, max_length=512)
    disabled: bool


def _disable_tools(read_only: bool = False):
    """(load_pool, identity_for, is_disabled, set_disabled) from core, or None on a core without the feature.

    ``read_only`` (the 30 s panel poll): rows come from the persisted pool, never ``load_pool`` (which can
    seed, heal and save auth.json), and nothing is read for a provider with no disable record."""
    try:
        from agent.credential_pool_disabled import disabled_keys, is_credential_disabled, set_credential_disabled
    except ImportError:
        return None
    from agent.account_usage_cache import _identity_id_for
    from agent.credential_pool import PooledCredential, load_pool
    if read_only:
        from hermes_cli.auth import read_credential_pool

        def load_pool(provider):  # noqa: F811 - read-only stand-in with the same .entries() shape
            ids, labels = disabled_keys(provider)[:2]
            rows = read_credential_pool(provider) if ids or labels else []
            entries = [PooledCredential.from_dict(provider, r) for r in (rows if isinstance(rows, list) else [])
                       if isinstance(r, dict)]
            return SimpleNamespace(entries=lambda: entries)
    return load_pool, _identity_id_for, is_credential_disabled, set_credential_disabled


def disabled_accounts(tools=None) -> Dict[str, Any]:
    """``{supported, accounts: {provider: [account_id, ...]}}``: account ids with every row disabled."""
    tools = tools if tools is not None else _disable_tools(read_only=True)
    if tools is None:
        return {'supported': False, 'accounts': {}}
    load_pool, identity_for, is_disabled, _set = tools
    out: Dict[str, list] = {}
    for provider in sorted(DISABLE_PROVIDERS):
        try:
            entries = load_pool(provider).entries()
        except Exception:
            continue  # one unreadable pool must not hide the others' state
        by_id: Dict[str, list] = {}
        for e in entries:
            by_id.setdefault(identity_for(provider, e), []).append(bool(is_disabled(provider, e)))
        ids = sorted(i for i, flags in by_id.items() if i and flags and all(flags))
        if ids:
            out[provider] = ids
    return {'supported': True, 'accounts': out}


def set_account_disabled(provider: str, account_id: str, disabled: bool, tools=None) -> Dict[str, Any]:
    """Disable/enable every pooled row of one account (the wire ``accounts[].id``). No secrets returned."""
    if provider not in DISABLE_PROVIDERS:
        raise HTTPException(400, detail=f'Disabling is not supported for {provider}')
    tools = tools if tools is not None else _disable_tools()
    if tools is None:
        raise HTTPException(501, detail='This Hermes core has no account disabling; update Hermes')
    load_pool, identity_for, is_disabled, set_disabled = tools
    try:
        matches = [e for e in load_pool(provider).entries() if identity_for(provider, e) == account_id]
    except Exception:
        raise HTTPException(503, detail='Credential pool is unavailable; retry') from None
    if not matches:
        raise HTTPException(404, detail='Account not found; refresh the panel')
    changed = sum(1 for e in matches if set_disabled(provider, e, disabled))
    still = [bool(is_disabled(provider, e)) for e in matches]
    # A global-root record can keep a row disabled after a profile-level enable.
    ok = all(still) if disabled else not any(still)
    return {'ok': ok, 'changed': changed, 'disabled': all(still)}


@router.get('/disabled')
def get_disabled():
    return disabled_accounts()


@router.post('/disabled')
def post_disabled(body: DisableBody):
    return set_account_disabled(body.provider, body.account_id, body.disabled)
