"""Mounted by Hermes at /api/plugins/compute-rentals (host owns authentication).
GET /inventory?session_id=&profile=&gateway_id=&refresh=false always returns the
full global machine list plus a separately filtered session_summary. Empty
gateway_id resolves to the persistent backend identity returned as gateway_id;
desktop connection IDs are not backend identities. An empty session id selects
nothing. When session_id and profile are both non-empty, running machines are
never auto-linked: the rent-machine skill attaches rentals explicitly.
POST/DELETE /bindings require that canonical gateway_id and accept:
{machine_key,gateway_id,profile,session_id}; only the local SQLite registry changes.
No cloud lifecycle operations or shell endpoints exist. Collection uses sync
routes so FastAPI runs blocking SDK calls in its thread pool. Startup/30-second
TTL refreshes run in the requesting thread, outside the snapshot lock; concurrent
readers get refreshing=true and incomplete totals. Manual refresh has a 2-second
cooldown; overlapping refreshes coalesce rather than block all readers.
"""
import importlib.util
from pathlib import Path
import sys
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, StrictStr


def _plugin():
    name = '_compute_rentals_backend'
    if name not in sys.modules:
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(name,root / '__init__.py',submodule_search_locations=[str(root)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name,None)
            raise
    return sys.modules[name]


def _autolink():
    # Loaded fresh with each import of this module (not via the cached
    # _compute_rentals_backend), so a plugin hot-reload picks up fixes.
    global _AUTOLINK
    if _AUTOLINK is None:
        path = Path(__file__).resolve().parents[1] / 'autolink.py'
        spec = importlib.util.spec_from_file_location('_compute_rentals_autolink_api', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _AUTOLINK = module
    return _AUTOLINK


_AUTOLINK = None


def auto_link(svc, session_id, profile):
    """Evidence-based auto-link (see autolink.py): only machines whose id,
    label, or ssh command appear in this session's (or its subagents') own
    tool calls. Best-effort; never blocks inventory reads.
    """
    try:
        _autolink().auto_link(svc, session_id, profile)
    except Exception:
        pass


class Binding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    machine_key: StrictStr
    gateway_id: StrictStr
    profile: StrictStr
    session_id: StrictStr


def create_router(service_factory=None):
    router = APIRouter()
    factory = service_factory or (lambda:_plugin().get_service())

    @router.get('/inventory')
    def inventory(session_id: str='', profile: str='', gateway_id: str='', refresh: bool=False):
        svc = factory()
        svc.start_refresh(force=refresh)
        return svc.inventory(session_id=session_id,profile=profile,gateway_id=gateway_id,refresh=False)

    def change(action, body):
        try:
            return _plugin().binding(factory(), action, body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post('/bindings')
    def attach(body: Binding):
        return change('attach',body)

    @router.delete('/bindings')
    def detach(body: Binding):
        return change('detach',body)
    return router


router = create_router()
