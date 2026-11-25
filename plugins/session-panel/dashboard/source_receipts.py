"""Private panel-source-receipts/1 custody; explicitly installed, never activated.

A stored model, invalidation, generic callback, or response construction is not
inclusion. Exact post-transform successful emission is labelled server emission,
not authenticated client delivery. Authenticated completed ASGI body writes are
labelled catch-up. Neither proves Desktop receipt or pixels. This private log
never supplies runtime truth or authorization.
"""
from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
import time
from uuid import uuid4

SCHEMA = 'panel-source-receipts/1'
IDENTITY_FIELDS = ('boot_id', 'pid', 'process_birth_ticks', 'runtime_sha', 'mods_sha',
                   'core_sha', 'api_generation', 'serving_gateway', 'profile')
EVENTS = frozenset(('source_received', 'source_error', 'capture_start', 'capture_end',
    'admitted', 'projection_start', 'projection_end', 'model_stored', 'emit_callback', 'emit_success',
    'emit_error', 'catchup_success', 'catchup_error', 'no_change', 'superseded',
    'forgotten', 'writer_request', 'writer_acquire', 'writer_release', 'window_end'))


def detached(value):
    """Exact JSON custody, including dataclass-keyed maps without string coercion."""
    if is_dataclass(value) and not isinstance(value, type):
        return {'type': type(value).__name__, **{f.name: detached(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, Mapping):
        if all(isinstance(k, str) for k in value):
            return {k: detached(v) for k, v in value.items()}
        return {'entries': [[detached(k), detached(v)] for k, v in value.items()]}
    if isinstance(value, (tuple, list)):
        return [detached(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((detached(v) for v in value), key=encode)
    if isinstance(value, bytes):
        return {'type': 'bytes', 'base64': base64.b64encode(value).decode('ascii')}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f'unsupported receipt value: {type(value).__name__}')


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode()


def payload_fields(payload):
    if not isinstance(payload, bytes):
        raise TypeError('receipt payload must be immutable bytes')
    return {'payload_sha256': hashlib.sha256(payload).hexdigest(), 'serialized_bytes': len(payload),
            'payload_b64': base64.b64encode(payload).decode('ascii')}


class ReceiptLog:
    """Bounded async append-only private log; overflow/error invalidates the window.

    identity is observed by the explicit activation owner, not from request data.
    Producers only detach/encode/enqueue. File I/O/fsync runs outside their locks.
    Every rotated segment is retained. close/flush belong outside Feed/SQL locks.
    """
    def __init__(self, path, *, identity, queue_bytes=16 * 1024 * 1024,
                 max_record_bytes=8 * 1024 * 1024, segment_bytes=64 * 1024 * 1024):
        if any(identity.get(k) is None for k in IDENTITY_FIELDS):
            raise ValueError('complete observed process/runtime identity required')
        if min(queue_bytes, max_record_bytes, segment_bytes) <= 0:
            raise ValueError('positive receipt bounds required')
        self.identity = detached(identity)
        self.window_id = uuid4().hex
        self.path = Path(path)
        self.queue_bytes, self.max_record_bytes, self.segment_bytes = queue_bytes, max_record_bytes, segment_bytes
        self.queue = queue.Queue()
        self.lock = threading.Lock()
        self.queued_bytes = self.dropped_records = 0
        self.write_errors = []
        self.encoding_errors = []
        self.closed = False
        self.thread = threading.Thread(target=self._run, name='panel-source-receipts', daemon=True)
        self.thread.start()

    @property
    def valid(self):
        with self.lock:
            return self.dropped_records == 0 and not self.write_errors

    def _value(self, event, data):
        current = threading.current_thread()
        birth = getattr(current, '_panel_receipt_birth', None)
        if birth is None:
            birth = uuid4().hex
            setattr(current, '_panel_receipt_birth', birth)
        return {**self.identity, **detached(data), 'schema': SCHEMA, 'event': event,
                'window_id': self.window_id, 'record_id': uuid4().hex,
                'wall_ns': time.time_ns(), 'monotonic_ns': time.monotonic_ns(),
                'thread_id': threading.get_native_id(), 'thread_birth': birth}

    def record(self, event, **data):
        if event not in EVENTS:
            raise ValueError('unknown source receipt event')
        # Common identities cannot be replaced by payload fields.
        reserved = set(IDENTITY_FIELDS) | {'schema', 'event', 'window_id', 'record_id', 'wall_ns', 'monotonic_ns',
                                           'thread_id', 'thread_birth'}
        if reserved.intersection(data):
            raise ValueError('receipt payload cannot override custody identity')
        try:
            raw = encode(self._value(event, data)) + b'\n'
        except (TypeError, ValueError) as exc:
            with self.lock:
                self.dropped_records += 1
                # Exception text here contains only the unsupported type or JSON
                # encoding category, not the rejected private value itself.
                if len(self.encoding_errors) < 25:
                    self.encoding_errors.append(str(exc))
            return False
        with self.lock:
            if self.closed or len(raw) > self.max_record_bytes or self.queued_bytes + len(raw) > self.queue_bytes:
                self.dropped_records += 1
                return False
            self.queued_bytes += len(raw)
            self.queue.put_nowait(raw)
        return True

    def flush(self, timeout=10):
        barrier = threading.Event()
        with self.lock:
            if self.closed:
                return not self.thread.is_alive() and self.valid_without_lock()
            self.queue.put_nowait(barrier)
        return barrier.wait(timeout) and self.valid

    def valid_without_lock(self):
        return self.dropped_records == 0 and not self.write_errors

    def close(self, timeout=10):
        with self.lock:
            if not self.closed:
                self.closed = True
                self.queue.put_nowait(None)
        self.thread.join(timeout)
        return not self.thread.is_alive() and self.valid

    def _open(self, index):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.path if index == 0 else self.path.with_name(f'{self.path.name}.{index}')
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, 'ab', buffering=0)

    def _run(self):
        stream, index, size = None, 0, 0
        try:
            while True:
                item = self.queue.get()
                terminal = item is None
                if terminal:
                    with self.lock:
                        counts = {'dropped_records': self.dropped_records, 'write_errors': list(self.write_errors),
                                  'encoding_errors': list(self.encoding_errors)}
                    item = encode(self._value('window_end', counts)) + b'\n'
                    with self.lock:
                        self.queued_bytes += len(item)
                try:
                    if isinstance(item, threading.Event):
                        if stream is not None:
                            os.fsync(stream.fileno())
                    else:
                        if stream is None or size + len(item) > self.segment_bytes:
                            if stream is not None:
                                os.fsync(stream.fileno()); stream.close()
                                index += 1
                            stream = self._open(index)
                            size = os.fstat(stream.fileno()).st_size
                        stream.write(item)
                        size += len(item)
                except Exception as exc:
                    with self.lock:
                        self.write_errors.append(type(exc).__name__)
                        self.dropped_records += int(isinstance(item, bytes))
                finally:
                    if isinstance(item, bytes):
                        with self.lock:
                            self.queued_bytes -= len(item)
                    else:
                        item.set()
                if terminal:
                    break
        finally:
            if stream is not None:
                try:
                    os.fsync(stream.fileno())
                except OSError as exc:
                    with self.lock:
                        self.write_errors.append(type(exc).__name__)
                stream.close()


def publication(feed, session, held):
    """Called only by the writer after merge; never consult a later mutable bundle."""
    p, b = held.get('board_projection'), held['bundle']
    return detached({'publication_id': uuid4().hex, 'feed_epoch': feed.epoch,
        'chat': p.admission.chat if p is not None else None, 'session': session,
        'chat_rev': held['rev'], 'model_revision': (b.get('model') or {}).get('revision'),
        'writer': getattr(feed._writer_context, 'current', None),
        'canonical_seq': b.get('canonical_seq'), 'computed_seq': b.get('computed_seq'),
        'computed_at': b.get('computed_at'), 'board_observed_at': b.get('board_observed_at'),
        'admission': p.admission if p is not None else None,
        'source_vector': p.input_versions if p is not None else None,
        'covered_scopes': p.covered_scopes if p is not None else None,
        'missing_scopes': p.missing_scopes if p is not None else None,
        'dependencies': p.dependencies if p is not None else None})


@dataclass(frozen=True)
class StoredModel:
    publication_json: bytes
    bundle: bytes

    @property
    def publication(self):
        return json.loads(self.publication_json)


def stored_model(feed, session, held, bundle):
    return StoredModel(encode(publication(feed, session, held)), bundle)


def completed_response(body, *, log, authenticated, request_id, publications=None, stored=(), media_type='application/json'):
    """Snapshot exact HTTP payload; acknowledge only after successful final send.

    Authentication is supplied by the server's core guard, never request fields.
    Failure before/at final send logs an error, including cancellation. The write
    clock is ASGI completion, not a claim of client receipt or native pixels.
    """
    from starlette.responses import Response
    payload = payload_fields(body)
    fixed_publications = encode(publications) if publications is not None else None
    fixed_stored = tuple(stored)
    response_id = uuid4().hex

    class CompletedResponse(Response):
        async def __call__(self, scope, receive, send):
            completed = False
            written_hash = hashlib.sha256()
            written_bytes = 0
            async def written(message):
                nonlocal completed, written_bytes
                await send(message)
                if message['type'] == 'http.response.body':
                    chunk = message.get('body', b'')
                    written_hash.update(chunk)
                    written_bytes += len(chunk)
                    if not message.get('more_body', False):
                        completed = True
            try:
                await super().__call__(scope, receive, written)
            except BaseException as exc:
                log.record('catchup_error', request_id=request_id, response_id=response_id,
                    authenticated=authenticated is True, error=type(exc).__name__, **payload)
                raise
            exact = (written_bytes == payload['serialized_bytes']
                     and written_hash.hexdigest() == payload['payload_sha256'])
            if completed and exact and authenticated is True:
                log.record('catchup_success', request_id=request_id, response_id=response_id,
                    authenticated=True, kind='model', write_completed_ns=time.monotonic_ns(),
                    publications=json.loads(fixed_publications) if fixed_publications is not None
                                 else [value.publication for value in fixed_stored], **payload)
            else:
                log.record('catchup_error', request_id=request_id, response_id=response_id,
                    authenticated=authenticated is True, error='unauthenticated_inexact_or_incomplete_write',
                    actual_serialized_bytes=written_bytes, actual_payload_sha256=written_hash.hexdigest(), **payload)
    return CompletedResponse(body, media_type=media_type)


def validate_records(records, *, require_closed=False):
    """Fail-closed inclusion audit, never an execution/activation permission.

    Exact stored client-bundle bytes must occur inside the emitted/HTTP payload.
    Invalidation/callback/construction alone never fulfills stored publications.
    Return unmatched publications as such; downstream latency analysis retains
    their original admission age and censoring rather than dropping failures.
    """
    errors, stored, fulfilled = [], {}, set()
    emitted, caught_up = set(), set()
    windows, ended, record_ids = {}, set(), set()
    for r in records:
        if not isinstance(r, dict) or r.get('schema') != SCHEMA or r.get('event') not in EVENTS:
            errors.append('invalid schema/event'); continue
        if any(r.get(k) is None for k in IDENTITY_FIELDS):
            errors.append('missing process/runtime identity'); continue
        try:
            # Python equality aliases JSON booleans, integers and floats. Runtime
            # custody must retain their exact encoded scalar types instead.
            custody_identity = encode(tuple(r[k] for k in IDENTITY_FIELDS))
        except (TypeError, ValueError):
            errors.append('invalid process/runtime identity'); continue
        window = r.get('window_id')
        record_id = r.get('record_id')
        if not isinstance(window, str) or not window or not isinstance(record_id, str) or not record_id:
            errors.append('missing window/record identity'); continue
        if record_id in record_ids or window in ended:
            errors.append('duplicate record or record after window closure'); continue
        record_ids.add(record_id)
        if window in windows and windows[window] != custody_identity:
            errors.append('runtime identity changed within window'); continue
        windows[window] = custody_identity
        event = r['event']
        if event == 'window_end':
            ended.add(window)
            if r.get('dropped_records') != 0 or r.get('write_errors') != []:
                errors.append('receipt window lost records')
        if event not in ('model_stored', 'emit_callback', 'emit_success', 'catchup_success'):
            continue
        try:
            raw = base64.b64decode(r['payload_b64'], validate=True)
            if len(raw) != r['serialized_bytes'] or hashlib.sha256(raw).hexdigest() != r['payload_sha256']:
                raise ValueError('payload mismatch')
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError('payload is not an object')
        except (KeyError, ValueError, TypeError):
            errors.append('missing/corrupt exact payload'); continue
        if event == 'model_stored':
            p = r.get('publication')
            if not isinstance(p, dict):
                errors.append('missing publication'); continue
            key = (p.get('feed_epoch'), p.get('publication_id'), p.get('session'), p.get('model_revision'))
            if (not all(isinstance(k, str) and k for k in key)
                    or not isinstance(p.get('admission'), dict) or not isinstance(p.get('source_vector'), dict)
                    or not isinstance(body.get('model'), dict) or body['model'].get('revision') != key[3]):
                errors.append('stored publication lacks exact scope/model custody'); continue
            if key in stored:
                errors.append('duplicate stored publication'); continue
            stored[key] = (p, body, custody_identity, window)
            continue
        if r.get('kind') != 'model':
            continue
        if event == 'emit_callback':
            continue  # an arbitrary callback is not the exact server adapter
        if event == 'emit_success':
            start, end = r.get('callback_started_ns'), r.get('callback_completed_ns')
            if (type(start) is not int or type(end) is not int or not 0 < start <= end
                    or r.get('observation') != 'server_emission'):
                errors.append('emit lacks exact post-transform callback custody'); continue
        if event == 'catchup_success' and (r.get('authenticated') is not True
                or not r.get('request_id') or not r.get('response_id') or not r.get('write_completed_ns')):
            errors.append('catchup lacks authenticated completed write'); continue
        publications = r.get('publications')
        if not isinstance(publications, list) or not isinstance(body.get('bundles'), dict):
            errors.append('missing scoped inclusion collection'); continue
        for p in publications:
            if not isinstance(p, dict):
                errors.append('invalid inclusion publication'); continue
            key = (p.get('feed_epoch'), p.get('publication_id'), p.get('session'), p.get('model_revision'))
            if not all(isinstance(k, str) and k for k in key):
                errors.append('invalid inclusion key'); continue
            original = stored.get(key)
            if original is None or original[2:] != (custody_identity, window):
                errors.append('inclusion has no exact stored vector'); continue
            try:
                exact_vector = encode(original[0]) == encode(p)
                exact_model = encode(body['bundles'].get(key[2])) == encode(original[1])
            except (TypeError, ValueError):
                errors.append('inclusion has invalid JSON custody'); continue
            if not exact_vector:
                errors.append('inclusion has no exact stored vector'); continue
            if body.get('epoch') != key[0] or not exact_model:
                errors.append('inclusion payload does not contain exact stored model'); continue
            fulfilled.add(key)
            (caught_up if event == 'catchup_success' else emitted).add(key)
    if require_closed and (not windows or set(windows) != ended):
        errors.append('missing terminal receipt-window accounting')
    return {'valid': not errors, 'errors': errors, 'stored': len(stored), 'fulfilled': len(fulfilled),
            'server_emitted': len(emitted), 'authenticated_catchup': len(caught_up),
            'unmatched': [list(k) for k in sorted(stored.keys() - fulfilled)]}


def main(argv=None):
    """Audit private JSONL segments in caller-provided order; never activate."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('segments', nargs='+', type=Path)
    parser.add_argument('--allow-open', action='store_true', help='streaming audit only; not a closed-window receipt')
    parser.add_argument('--require-fulfilled', action='store_true', help='fail if any stored publication remains unmatched')
    args = parser.parse_args(argv)
    records = []
    try:
        for path in args.segments:
            with path.open(encoding='utf-8') as stream:
                records.extend(json.loads(line) for line in stream if line.strip())
        result = validate_records(records, require_closed=not args.allow_open)
    except (OSError, ValueError, TypeError) as exc:
        result = {'valid': False, 'errors': [type(exc).__name__], 'unmatched': []}
    result['acceptance'] = 'not_evaluated'  # schema/inclusion audit is not the B/C latency or CPU gate
    print(json.dumps(result, sort_keys=True))
    return 0 if result['valid'] and not (args.require_fulfilled and result['unmatched']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
