"""Record the pooled credential that served each successful main-agent API call.

Hermes' ``post_api_request`` hook does not pass the agent, so we find the calling agent on the
stack (the hook runs synchronously on the agent thread) and read the dispatched credential id the
same way core's rate-limit recovery does (``agent._credential_pool_entry_id``, else
``pool.current()``). Result goes to ``$HERMES_HOME/account_usage_last.json``:
``{provider: {"entry_id", "label", "at"}}``. Fail-open: any error is swallowed.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import weakref

_lock = threading.Lock()
_last: dict = {}
_observed_requests: dict = {}


def _state_path() -> str:
    try:
        from hermes_constants import get_hermes_home
        return os.path.join(str(get_hermes_home()), "account_usage_last.json")
    except Exception:
        return os.path.expanduser("~/.hermes/account_usage_last.json")


_agents: "weakref.WeakValueDictionary[str, object]" = weakref.WeakValueDictionary()


def _agent_in(frame, session_id: str, max_depth: int = 40):
    for _ in range(max_depth):
        if frame is None:
            return None
        loc = frame.f_locals
        for key in ("agent", "self"):
            cand = loc.get(key)
            if cand is not None and hasattr(cand, "_credential_pool") and (
                    not session_id or getattr(cand, "session_id", None) == session_id):
                return cand
        frame = frame.f_back
    return None


def _find_agent(session_id: str):
    """The hook may run on a bounded worker thread while the agent thread blocks waiting on it,
    so look at our own stack first, then every thread's stack, for the agent of this session."""
    cached = _agents.get(session_id) if session_id else None
    if cached is not None:
        return cached
    found = _agent_in(sys._getframe(1), session_id)
    if found is None:
        me = threading.get_ident()
        for tid, frame in sys._current_frames().items():
            if tid != me and (found := _agent_in(frame, session_id)) is not None:
                break
    if found is not None and session_id:
        try:
            _agents[session_id] = found
        except TypeError:
            pass
    return found


def _on_api(**kw):
    try:
        request_id = kw.get('api_request_id')
        with _lock:
            if request_id and (_state_path(), request_id) in _observed_requests:
                return None
        agent = _find_agent(str(kw.get("session_id") or ""))
        if agent is not None and getattr(agent, '_account_usage_observed_request', None) == kw.get('api_request_id') and kw.get('api_request_id'):
            return None  # already observed the actual request-local credential
        pool = getattr(agent, "_credential_pool", None) if agent is not None else None
        if pool is None:
            return None
        entry_id = getattr(agent, "_credential_pool_entry_id", None)
        entry = None
        if isinstance(entry_id, str) and entry_id:
            entry = next((e for e in pool.entries() if e.id == entry_id), None)
        if entry is None:
            entry = pool.current()
        if entry is None:
            return None
        provider = str(kw.get("provider") or getattr(agent, "provider", "") or "").lower()
        if not provider:
            return None
        now = time.time()
        rec = {"entry_id": entry.id, "label": str(entry.label or ""), "at": now}
        with _lock:
            path = _state_path()
            prev = _last.get((path, provider))
            if prev and prev["entry_id"] == rec["entry_id"] and now - prev["at"] < 20:
                return None
            _last[(path, provider)] = rec
            try:
                with open(path) as fh:
                    data = json.load(fh)
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}
            data[provider] = rec
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w") as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
    except Exception:
        pass
    return None


def _observe_selected(agent, snapshot):
    """Write request-specific identity synchronously, before ContextVar teardown.

    The later observer hook must not overwrite it with the unchanged shared key.
    Observation failures never change a successful provider result.
    """
    agent._account_usage_observed_request = getattr(agent, '_account_usage_request_id', None)
    try:
        pool = getattr(agent, '_credential_pool', None)
        entry = next((e for e in pool.entries() if e.id == snapshot.entry_id), None) if pool else None
        rec = dict(entry_id=snapshot.entry_id, account_id=snapshot.account_id,
                   label=str(entry.label or '') if entry else '', at=time.time())
        with _lock:
            path = _state_path()
            request_id = getattr(agent, '_account_usage_request_id', None)
            if request_id:
                _observed_requests[(path, request_id)] = time.monotonic()
                while len(_observed_requests) > 1024:
                    _observed_requests.pop(next(iter(_observed_requests)))
            try:
                with open(path) as fh:
                    data = json.load(fh)
                if not isinstance(data, dict):
                    data = {}
            except (OSError, ValueError):
                data = {}
            data[snapshot.provider] = rec
            _last[(path, snapshot.provider)] = rec
            tmp = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
            with open(tmp, 'w') as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
    except Exception:
        pass


def register(ctx):
    ctx.register_hook("post_api_request", _on_api)
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('account_usage_execution_api', Path(__file__).parent / 'dashboard/plugin_api.py')
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    selection = api.load_selection_module()
    if not hasattr(selection, 'ExecutionIntegration'):
        # The earlier API-only version may already be cached in this gateway.
        # It installed no factories or middleware. Upgrade that inert module IN
        # PLACE so existing mounted API handlers retain the same module object.
        # Never replace/re-execute an execution-capable module with live contexts.
        selection.__spec__.loader.exec_module(selection)
    integration = selection.ExecutionIntegration(observe=_observe_selected)
    try:
        integration.install()
    except Exception:
        # Preserve observation and a fail-closed refusal path for queued intents.
        # Never emit raw loader exceptions: custom factories may carry secrets.
        import logging
        integration.disable()
        logging.getLogger(__name__).warning(integration.error)
    ctx.on_unload(integration.unload)
    ctx.register_middleware('llm_execution', integration.middleware)

