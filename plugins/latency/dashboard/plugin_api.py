"""Authenticated dashboard routes; expensive reads stay off the event loop."""
import asyncio
from contextlib import asynccontextmanager
import importlib
import importlib.util
from pathlib import Path
import sys
import threading
import time

from fastapi import APIRouter, HTTPException, Query, Request

_lock = threading.RLock()
_cache = {}
RANGES = {'24h': 86400, '7d': 7*86400, '30d': 30*86400, '90d': 90*86400}
MAX_BODY = 256*1024


def pkg():
    name = 'hermes_latency_plugin'
    with _lock:
        if name not in sys.modules:
            root = Path(__file__).resolve().parents[1]
            spec = importlib.util.spec_from_file_location(name, root/'__init__.py', submodule_search_locations=[str(root)])
            module = importlib.util.module_from_spec(spec)
            # Relative imports during __init__ require a package entry. Lock
            # excludes other callers until execution has completed.
            sys.modules[name] = module
            try:
                spec.loader.exec_module(module)
            except BaseException:
                sys.modules.pop(name, None)
                raise
        return sys.modules[name]


def services():
    package = pkg().__name__
    sampler = importlib.import_module(package+'.sampler')
    registry = importlib.import_module(package+'.registry')
    store = importlib.import_module(package+'.store')
    root = sampler.home()
    return sampler, registry, store.Store(root/'cache/latency/series.sqlite'), root


@asynccontextmanager
async def lifespan(app):
    sampler, _, _, root = services()
    sampler.start(root)
    try:
        yield
    finally:
        await asyncio.to_thread(sampler.stop)


router = APIRouter(lifespan=lifespan)


def build(range_key, metric=None, now=None):
    sampler, definitions, store, root = services()
    now = time.time() if now is None else now
    entries = definitions.registry(root)
    if metric and metric not in {d['id'] for d in entries}:
        raise ValueError('unknown metric')
    rows = []
    for definition in entries:
        if metric and definition['id'] != metric:
            continue
        key = 'measured_since:'+definition['id']
        measured = store.meta_get(key)
        if measured is None:
            measured = now
            store.meta_set(key, measured)
        view = store.view(definition['id'], now-RANGES[range_key], now)
        rows.append({**definition, 'measured_since': measured, **view})
    return dict(api=1, range=range_key, at=now, timezone='America/Los_Angeles',
                quantile_relative_error=0.005, bucket_seconds=3600, metrics=rows,
                collection=store.meta_get('last_cycle'))


def cached(range_key, metric=None):
    # At most 17 registry metric filters, four ranges. Unknown keys never cache.
    with _lock:
        key = (range_key, metric)
        previous = _cache.get(key)
        if previous and time.monotonic()-previous[0] < 15:
            return previous[1]
        result = build(range_key, metric)
        _cache[key] = (time.monotonic(), result)
        return result


@router.get('/ping')
async def ping():
    return {'ok': True, 'api': 1}


@router.get('/v1/series')
async def series(range: str = Query('7d'), metric: str | None = Query(None)):
    if range not in RANGES:
        raise HTTPException(400, 'range must be 24h, 7d, 30d or 90d')
    try:
        return await asyncio.to_thread(cached, range, metric)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def ingest(body):
    if not isinstance(body, dict) or set(body) != {'spans'} or not isinstance(body['spans'], list) or not 1 <= len(body['spans']) <= 200:
        raise ValueError('expected {spans: [1..200 span objects]}')
    _, definitions, store, root = services()
    allowed = {d['id'] for d in definitions.registry(root) if d['kind'] == 'span'}
    if any(not isinstance(s, dict) or s.get('metric') not in allowed for s in body['spans']):
        raise ValueError('unknown or non-span metric')
    spans = importlib.import_module(pkg().__name__+'.spans')
    normalized = spans.append_batch(body['spans'], root=root)
    inserted = store.ingest(normalized)
    with _lock:
        _cache.clear()
    return {'ok': True, 'accepted': len(normalized), 'inserted': inserted}


@router.post('/v1/spans')
async def post_spans(request: Request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY:
            raise HTTPException(400, 'span batch too large')
    try:
        import json
        body = json.loads(raw)
        return await asyncio.to_thread(ingest, body)
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error)) from error
