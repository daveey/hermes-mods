"""GET /api/plugins/account-usage/last -> {provider: {entry_id, label, at}} (no secrets)."""
from __future__ import annotations

import json
import os
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
