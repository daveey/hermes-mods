"""Authenticated dashboard routes; expensive reads stay off the event loop."""
import asyncio
from contextlib import asynccontextmanager
import importlib
import importlib.util
import json
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
    try:
        sources = json.loads((root/'latency/sources.json').read_text())
        if not isinstance(sources, dict) or not isinstance(sources.get('github', {}), dict):
            sources = {}
    except (OSError, ValueError):
        sources = {}
    links = importlib.import_module(pkg().__name__+'.ledger')
    for definition in entries:
        if metric and definition['id'] != metric:
            continue
        key = 'measured_since:'+definition['id']
        measured = store.meta_get(key)
        if measured is None:
            measured = now
            store.meta_set(key, measured)
        view = store.view(definition['id'], now-({'8d': 8*86400}.get(range_key) or RANGES[range_key]), now)
        row = links.enrich({**definition, 'measured_since': measured, **view}, sources)
        if definition['id'] == 'turn_first':
            turns = importlib.import_module(pkg().__name__+'.turn_stages')
            trace = turns.latest(store, now-RANGES.get(range_key, 8*86400), now)
            row.update(turn_trace=trace, stages=trace['stages'])
        rows.append(row)
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


def ledger(range_key):
    module = importlib.import_module(pkg().__name__+'.ledger')
    return module.build(cached(range_key), cached('8d'), cached('24h'))


@router.get('/v1/ledger')
async def get_ledger(range: str = Query('7d')):
    if range not in RANGES:
        raise HTTPException(400, 'invalid range')
    return await asyncio.to_thread(ledger, range)


from fastapi import WebSocket, WebSocketDisconnect


@router.websocket('/v1/events')
async def ledger_events(ws: WebSocket):
    # Reuse canonical dashboard authentication; absence/failure is closed.
    try:
        from hermes_cli.web_server_chat import _ws_auth_ok
        authorized = _ws_auth_ok(ws)
    except Exception:
        authorized = False
    range_key = ws.query_params.get('range', '7d')
    if not authorized or range_key not in RANGES:
        await ws.close(code=1008)
        return
    await ws.accept()
    import json
    previous = None
    try:
        while True:
            payload = await asyncio.to_thread(ledger, range_key)
            # Observation clock changes every cache cycle; don't send unchanged data.
            content = json.dumps({key: value for key, value in payload.items() if key != 'at'}, sort_keys=True)
            if content != previous:
                await ws.send_json(payload)
                previous = content
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=15)
            except asyncio.TimeoutError:
                pass
    except (WebSocketDisconnect, RuntimeError):
        return


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
    turns = importlib.import_module(pkg().__name__+'.turn_stages')
    allowed = {d['id'] for d in definitions.registry(root) if d['kind'] == 'span'} | turns.METRICS
    spans = importlib.import_module(pkg().__name__+'.spans')
    validated = [spans.validate_span(sample) for sample in body['spans']]
    if any(s['metric'] not in allowed for s in validated):
        raise ValueError('unknown or non-span metric')
    if any(s['metric'] in turns.METRICS and not turns.valid(s) for s in validated):
        raise ValueError('invalid turn stage')
    normalized = spans.append_batch(validated, root=root)
    inserted = store.ingest([s for s in normalized if s['metric'] not in turns.METRICS])
    inserted += turns.ingest(store, normalized, time.time())
    with _lock:
        _cache.clear()
    exporter = importlib.import_module(pkg().__name__+'.exporter')
    exporter.enqueue(normalized)
    return {'ok': True, 'accepted': len(normalized), 'inserted': inserted}


@router.post('/v1/spans')
async def post_spans(request: Request):
    return await read_spans(request)


@router.post('/v1/otlp/traces')
async def post_otlp(request: Request):
    return await read_spans(request, otlp=True)


@router.get('/v1/metrics')
async def metrics():
    def read():
        _, definitions, store, root = services()
        module = importlib.import_module(pkg().__name__+'.prometheus')
        return module.render(store, definitions.registry(root))
    from fastapi.responses import Response
    return Response(await asyncio.to_thread(read), media_type='text/plain; version=0.0.4')


async def read_spans(request, otlp=False):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY:
            raise HTTPException(400, 'span batch too large')
    try:
        import json
        body = json.loads(raw)
        if otlp:
            bridge = importlib.import_module(pkg().__name__+'.otel')
            body = {'spans': bridge.from_otlp(body)}
        return await asyncio.to_thread(ingest, body)
    except (ValueError, TypeError, RecursionError) as error:
        raise HTTPException(400, str(error)) from error
