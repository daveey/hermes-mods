"""Pure strict stage-evidence audit for private ``panel-source-receipts/1`` records (UNACTIVATED).

``audit(records)`` is an evidence-completeness gate, independent of the semantic
oracle and of ``source_receipts.validate_records`` (byte inclusion). It performs no
I/O, takes no locks, never raises on malformed input and does not weaken or change
the schema. A ``valid`` result means only that every stored publication has a
complete, exactly joined chain of custody (source_received -> admission ->
projection start/end -> writer request/acquire/release -> model_stored, plus
emit_success where emitted). It proves no row/action/absence correctness, latency
acceptance or delivery.

Joins use identities and the writer acquisition interval, never record position:
``projection_end`` is logged after ``model_stored``/emit. Equality is canonical
JSON, type-sensitive (``true``, ``1`` and ``1.0`` differ). Unfulfilled failed
source/capture/emit/writer/projection records are counted as censored diagnostics
and never promoted to success. A scope listed as missing/unknown is never counted
as authoritative absence. Work is bounded (single indexing pass, bisect joins).
"""
from __future__ import annotations

import base64
import hashlib
import json
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict

SCHEMA = 'panel-source-receipts/1'
IDENTITY_FIELDS = ('boot_id', 'pid', 'process_birth_ticks', 'runtime_sha', 'mods_sha',
                   'core_sha', 'api_generation', 'serving_gateway', 'profile', 'window_id')
MAX_RECORDS = 500_000
MAX_NODES = 5_000_000          # total JSON nodes canonicalized
MAX_DEPTH = 64
MAX_JOIN_WORK = 4_000_000
MAX_ERRORS = 200               # retained messages; counts['error_total'] is exact
SUCCESS_DISPOSITIONS = ('stored', 'no_change')
RECEIVED_OK = ('accepted', 'observation_only')
PRODUCER = 'board_projection'


class _Bad(Exception):
    pass


class _Dup(Exception):
    pass


def _pairs_hook(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise _Dup(k)
        d[k] = v
    return d


def _no_const(name):
    raise ValueError('non-finite constant ' + name)


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float('inf'), float('-inf'))


def _str(v):
    return isinstance(v, str) and bool(v)


class _Audit:
    def __init__(self, max_errors):
        self.errors, self.codes, self.max_errors = [], Counter(), max_errors
        self.counts = Counter()
        self.nodes = 0
        self.work = 0
        self._canon = {}
        self.stop = False
        self.seen = set()

    def err(self, code, detail=''):
        self.codes[code] += 1
        if len(self.errors) < self.max_errors:
            self.errors.append(f'{code}: {detail}' if detail else code)

    def once(self, code, key, detail):
        """Report a defect shared by many records (e.g. one absent receipt) a single time."""
        if (code, key) in self.seen:
            self.counts['repeat_' + code] += 1
            return
        self.seen.add((code, key))
        self.err(code, detail)

    def tick(self, n=1):
        self.work += n
        if self.work > MAX_JOIN_WORK and not self.stop:
            self.stop = True
            self.err('work_bound_exceeded', f'join work > {MAX_JOIN_WORK}')
        return not self.stop

    def canon(self, v):
        """Canonical type-sensitive JSON text, or raise _Bad. Memoized per object."""
        key = id(v)
        hit = self._canon.get(key)
        if hit is not None and hit[0] is v:
            return hit[1]
        stack = [(v, 0)]
        while stack:
            x, d = stack.pop()
            self.nodes += 1
            if self.nodes > MAX_NODES or d > MAX_DEPTH:
                raise _Bad('bounds')
            if x is None or isinstance(x, (bool, str)):
                continue
            if isinstance(x, int):
                continue
            if isinstance(x, float):
                if not _num(x):
                    raise _Bad('non-finite number')
                continue
            if isinstance(x, list):
                stack.extend((i, d + 1) for i in x)
            elif isinstance(x, dict):
                for k, i in x.items():
                    if not isinstance(k, str):
                        raise _Bad('non-string key')
                    stack.append((i, d + 1))
            else:
                raise _Bad('non-JSON type ' + type(x).__name__)
        try:
            text = json.dumps(v, sort_keys=True, separators=(',', ':'), allow_nan=False)
        except (TypeError, ValueError, RecursionError) as e:
            raise _Bad(str(e))
        self._canon[key] = (v, text)
        return text

    def same(self, a, b):
        try:
            return self.canon(a) == self.canon(b)
        except _Bad:
            return False

    def entries(self, v, where, *, allow_empty_dict=True):
        """Dataclass-keyed map -> {canonical key: (key, value)}; None on malformed."""
        if v == {} and type(v) is dict and allow_empty_dict:
            return {}
        if not (isinstance(v, dict) and set(v) == {'entries'} and isinstance(v['entries'], list)):
            self.err('malformed_entries', where)
            return None
        out = {}
        for e in v['entries']:
            if not (isinstance(e, list) and len(e) == 2):
                self.err('malformed_entries', where)
                return None
            try:
                k = self.canon(e[0])
            except _Bad:
                self.err('malformed_entries', where)
                return None
            if k in out:
                self.err('duplicate_entry_key', where)
                return None
            out[k] = (e[0], e[1])
        return out


def _source_key_ok(k):
    return (isinstance(k, dict) and k.get('type') == 'SourceKey'
            and all(_str(k.get(f)) for f in ('board', 'canonical_path', 'gateway', 'producer', 'profile')))


def _version_ok(v):
    return (isinstance(v, dict) and v.get('type') == 'SourceVersion' and _int(v.get('source_generation'))
            and _int(v.get('source_revision')) and _str(v.get('store_epoch_uuid')))


def _vkey(v):
    return (v['source_generation'], v['source_revision'])


def _chat_ok(c):
    return (isinstance(c, dict) and c.get('type') == 'ChatKey'
            and all(_str(c.get(f)) for f in ('profile', 'serving_gateway', 'session_id')))


ADMISSION_FIELDS = ('chat', 'dirty_keys', 'first_admitted_mono_ns', 'first_admitted_wall_ns', 'id',
                    'lineage_generation', 'missing_scopes', 'missing_versions', 'reasons', 'receipt_ids',
                    'required_failures', 'required_scopes', 'required_versions', 'type', 'watch_token')


def audit(records, *, max_errors=MAX_ERRORS):
    """Strictly audit evidence completeness; return ``dict(valid, errors, counts)``.

    ``records`` is an iterable of decoded dicts and/or JSON text lines. Malformed
    JSON, duplicate keys, non-object records, missing fields and mismatched joins
    become explicit ``errors`` entries (``code: detail``); nothing is raised.
    ``valid`` is True only with zero errors and at least one stored publication.
    ``counts`` carries exact tallies, ``error_codes`` and censored diagnostics
    (unfulfilled failed records), which never count as errors or success.
    """
    A = _Audit(max_errors)
    try:
        _run(A, records)
    except Exception as e:  # defensive: the gate must fail closed, never raise
        A.err('audit_internal_error', type(e).__name__)
    counts = dict(A.counts)
    counts['error_total'] = sum(A.codes.values())
    counts['error_codes'] = dict(sorted(A.codes.items()))
    counts['errors_truncated'] = counts['error_total'] > len(A.errors)
    counts['canonical_nodes'] = A.nodes
    counts['join_work'] = A.work
    valid = counts['error_total'] == 0 and A.counts['publications'] > 0
    if counts['error_total'] == 0 and not valid:
        A.err('no_publications', 'no model_stored publication to audit')
        counts['error_total'] = 1
        counts['error_codes'] = {'no_publications': 1}
    return dict(valid=valid, errors=list(A.errors), counts=counts)


# ---------------------------------------------------------------- normalisation

def _normalize(A, records):
    out = []
    try:
        if isinstance(records, (str, bytes, bytearray)):
            records = records.decode('utf-8', 'replace').splitlines() if not isinstance(records, str) else records.splitlines()
        it = iter(records)
    except TypeError:
        A.err('records_not_iterable', type(records).__name__)
        return out
    for i, r in enumerate(it):
        if len(out) >= MAX_RECORDS:
            A.err('record_bound_exceeded', f'more than {MAX_RECORDS} records')
            break
        if isinstance(r, (bytes, bytearray)):
            try:
                r = bytes(r).decode('utf-8')
            except UnicodeDecodeError:
                A.err('malformed_json', f'record {i}: not UTF-8')
                continue
        if isinstance(r, str):
            if not r.strip():
                continue
            try:
                r = json.loads(r, object_pairs_hook=_pairs_hook, parse_constant=_no_const)
            except _Dup as e:
                A.err('malformed_json', f'record {i}: duplicate key {str(e)[:40]!r}')
                continue
            except (ValueError, RecursionError) as e:
                A.err('malformed_json', f'record {i}: {str(e)[:60]}')
                continue
        if not isinstance(r, dict):
            A.err('record_not_object', f'record {i}: {type(r).__name__}')
            continue
        try:
            A.canon(r)
        except _Bad as e:
            A.err('record_not_canonical_json', f'record {i}: {e}')
            continue
        out.append((i, r))
    return out


def _run(A, records):
    recs = _normalize(A, records)
    A.counts['records'] = len(recs)
    ident, by_event = {}, defaultdict(list)
    for i, r in recs:
        if r.get('schema') != SCHEMA:
            A.err('schema_mismatch', f'record {i}')
            continue
        e = r.get('event')
        if not _str(e):
            A.err('event_missing', f'record {i}')
            continue
        for f in IDENTITY_FIELDS:
            if r.get(f) is None:
                A.err('identity_missing', f'{e} record {i}: {f}')
                continue
            try:
                c = A.canon(r[f])
            except _Bad:
                A.err('identity_missing', f'{e} record {i}: {f}')
                continue
            if f in ident and ident[f] != c:
                A.err('identity_mismatch', f'{e} record {i}: {f} differs within window')
            ident.setdefault(f, c)
        if not _int(r.get('monotonic_ns')) or not _int(r.get('thread_id')) or not _str(r.get('thread_birth')):
            A.err('clock_or_thread_missing', f'{e} record {i}')
            continue
        by_event[e].append((i, r))
    if A.stop:
        return
    A.counts['unknown_events'] = sum(len(v) for k, v in by_event.items() if k not in _KNOWN)

    caps = _index_captures(A, by_event)
    receipts = _index_receipts(A, by_event, caps)
    admissions = _check_admissions(A, by_event, caps, receipts)
    writers = _index_writers(A, by_event)
    projections = _index_projections(A, by_event)
    _check_publications(A, by_event, caps, receipts, writers, projections)
    _check_emits(A, by_event, writers)
    _count_diagnostics(A, by_event, caps, projections, writers)


_KNOWN = frozenset(('source_received', 'source_error', 'capture_start', 'capture_end', 'admitted',
                    'projection_start', 'projection_end', 'model_stored', 'emit_callback', 'emit_success',
                    'emit_error', 'catchup_success', 'catchup_error', 'no_change', 'superseded',
                    'forgotten', 'writer_request', 'writer_acquire', 'writer_release', 'window_end'))


# ---------------------------------------------------------------- captures / receipts

def _index_captures(A, by_event):
    caps = {}
    for i, r in by_event.get('capture_start', ()):
        a = r.get('capture_attempt_id')
        if not _str(a):
            A.err('capture_attempt_missing', f'capture_start record {i}')
            continue
        if a in caps and caps[a].get('start') is not None:
            A.err('duplicate_capture_start', a)
            continue
        caps.setdefault(a, {})['start'] = (i, r)
    for i, r in by_event.get('capture_end', ()):
        a = r.get('capture_attempt_id')
        if not _str(a):
            A.err('capture_attempt_missing', f'capture_end record {i}')
            continue
        d = caps.setdefault(a, {})
        if d.get('end') is not None:
            A.err('duplicate_capture_end', a)
            continue
        d['end'] = (i, r)
    return caps


def _check_receipt_shape(A, i, r):
    """Return (capture_id, source, version, completed{canon->scope}, unknown{canon}) or None."""
    w = f'source_received record {i}'
    rc = r.get('receipt')
    ok = True
    if not _str(r.get('capture_attempt_id')) or not _str(r.get('owner_token')):
        A.err('source_received_incomplete', f'{w}: capture_attempt_id/owner_token'); ok = False
    if not _source_key_ok(r.get('source')):
        A.err('source_received_incomplete', f'{w}: source key'); ok = False
    if not isinstance(rc, dict) or rc.get('type') != 'Receipt':
        A.err('source_received_incomplete', f'{w}: receipt'); return None
    if not _version_ok(rc.get('version')):
        A.err('source_received_incomplete', f'{w}: receipt.version incomplete'); ok = False
    if not _source_key_ok(rc.get('source')) or (ok and not A.same(rc['source'], r['source'])):
        A.err('source_received_mismatch', f'{w}: receipt.source != record source'); ok = False
    if rc.get('owner_token') != r.get('owner_token'):
        A.err('source_received_mismatch', f'{w}: receipt owner_token'); ok = False
    for f in ('received_mono_ns', 'received_wall_ns', 'read_started_ns', 'read_finished_ns'):
        if not _int(rc.get(f)):
            A.err('source_received_incomplete', f'{w}: receipt.{f}'); ok = False
    if ok and not (rc['read_started_ns'] <= rc['read_finished_ns'] and rc['received_mono_ns'] <= r['monotonic_ns']):
        A.err('source_received_clock', f'{w}: read/receive clocks inconsistent'); ok = False
    cov = rc.get('coverage')
    if not isinstance(cov, dict) or cov.get('type') != 'Coverage' or not _str(cov.get('capture_id')):
        A.err('source_received_incomplete', f'{w}: coverage/capture_id'); return None
    demands = A.entries(cov.get('captured_demands'), w + ' captured_demands', allow_empty_dict=False)
    done, unknown = cov.get('completed_scopes'), cov.get('unknown_scopes')
    if demands is None or not isinstance(done, list) or not isinstance(unknown, list):
        A.err('source_received_incomplete', f'{w}: captured_demands/completed_scopes/unknown_scopes'); return None
    try:
        dk = set(demands)
        comp = {A.canon(s): s for s in done}
        unk = {A.canon(s) for s in unknown}
    except _Bad:
        A.err('source_received_incomplete', f'{w}: scopes not canonical'); return None
    if not comp.keys() <= dk or not unk <= dk or comp.keys() & unk:
        A.err('source_received_incomplete', f'{w}: completed/unknown scopes not a disjoint subset of captured demands')
        return None
    if (dk - comp.keys()) - unk:
        A.err('source_received_incomplete', f'{w}: captured scope neither completed nor unknown'); return None
    if not ok:
        return None
    return cov['capture_id'], rc['source'], rc['version'], comp, unk


def _index_receipts(A, by_event, caps):
    receipts = {}
    for i, r in by_event.get('source_received', ()):
        A.counts['source_received'] += 1
        shape = _check_receipt_shape(A, i, r)
        att = r.get('capture_attempt_id')
        c = caps.get(att)
        if c is None or c.get('start') is None:
            A.err('source_received_unjoined', f'record {i}: no capture_start for attempt')
        else:
            si, s = c['start']
            for f in ('thread_id', 'thread_birth', 'owner_token', 'feed_epoch', 'domain'):
                if s.get(f) != r.get(f):
                    A.err('source_received_unjoined', f'record {i}: {f} differs from capture_start')
            if not A.same(s.get('source'), r.get('source')):
                A.err('source_received_unjoined', f'record {i}: source differs from capture_start')
            if s['monotonic_ns'] > r['monotonic_ns']:
                A.err('source_received_clock', f'record {i}: before capture_start')
            e = c.get('end')
            if e is not None:
                for f in ('thread_id', 'thread_birth', 'owner_token'):
                    if e[1].get(f) != r.get(f):
                        A.err('source_received_unjoined', f'record {i}: {f} differs from capture_end')
                if e[1]['monotonic_ns'] < r['monotonic_ns']:
                    A.err('source_received_clock', f'record {i}: after capture_end')
                if isinstance(e[1].get('receipt'), dict) and not A.same(e[1]['receipt'], r.get('receipt')):
                    A.err('source_received_mismatch', f'record {i}: capture_end.receipt differs')
        if shape is None:
            continue
        cid = shape[0]
        if cid in receipts:
            A.err('duplicate_receipt_id', cid)
            continue
        receipts[cid] = dict(rec=(i, r), source=shape[1], version=shape[2], completed=shape[3],
                             unknown=shape[4], attempt=att,
                             demands=A.entries(r['receipt']['coverage']['captured_demands'], 'captured demands'))
    return receipts


# ---------------------------------------------------------------- admissions

def _admission(A, a, where):
    """Validate an Admission object; return parsed dict or None."""
    if not isinstance(a, dict) or a.get('type') != 'Admission':
        A.err('admission_malformed', f'{where}: not an Admission'); return None
    miss = [f for f in ADMISSION_FIELDS if f not in a]
    if miss:
        A.err('admission_malformed', f'{where}: missing {",".join(miss)}'); return None
    ok = _str(a['id']) and _str(a['watch_token']) and _chat_ok(a['chat']) \
        and _int(a['first_admitted_mono_ns']) and _int(a['first_admitted_wall_ns']) \
        and _int(a['lineage_generation']) and isinstance(a['receipt_ids'], list) \
        and all(_str(x) for x in a['receipt_ids']) and len(set(a['receipt_ids'])) == len(a['receipt_ids']) \
        and isinstance(a['dirty_keys'], list)
    if not ok:
        A.err('admission_malformed', f'{where}: field types'); return None
    rs = A.entries(a['required_scopes'], where + ' required_scopes')
    rv = A.entries(a['required_versions'], where + ' required_versions')
    ms = A.entries(a['missing_scopes'], where + ' missing_scopes')
    mv = A.entries(a['missing_versions'], where + ' missing_versions')
    if None in (rs, rv, ms, mv):
        return None
    for k, (key, v) in rv.items():
        if not _source_key_ok(key) or not _version_ok(v):
            A.err('admission_malformed', f'{where}: required_versions entry incomplete'); return None
    for k, (key, v) in rs.items():
        if not _source_key_ok(key) or not isinstance(v, dict) or v.get('type') != 'ScopeDemand':
            A.err('admission_malformed', f'{where}: required_scopes entry incomplete'); return None
    return dict(a=a, id=a['id'], rs=rs, rv=rv, ms=ms, mv=mv, receipts=list(a['receipt_ids']))


def _stable_equal(A, x, y):
    return all(A.same(x['a'].get(f), y['a'].get(f)) for f in
               ('id', 'chat', 'watch_token', 'first_admitted_mono_ns', 'first_admitted_wall_ns'))


def _attest(A, adm, receipts, where, publication):
    """Receipt joins for one admission; ``publication`` additionally demands version attestation."""
    got = []
    for rid in adm['receipts']:
        rc = receipts.get(rid)
        if rc is None:
            A.once('receipt_without_source_received', rid,
                   f'{where}: admitted receipt_id {rid} has no source_received (further uses suppressed)')
            continue
        got.append(rc)
        sk = A.canon(rc['source'])
        req = adm['rv'].get(sk)
        if req is not None:
            v, rq = rc['version'], req[1]
            if v['store_epoch_uuid'] != rq['store_epoch_uuid'] or _vkey(v) > _vkey(rq):
                A.err('receipt_version_exceeds_admission', f'{where}: receipt {rid}')
    if not publication or A.stop:
        return
    # every required (source, version) must be attested by an admitted receipt that completed
    # (not merely left unknown) the admission's demanded scopes for that source
    for sk, (key, ver) in adm['rv'].items():
        demands = {A.canon(v['id']): v for _, (kk, v) in adm['rs'].items() if A.same(kk, key)}
        need = set(demands)
        hit = False
        for rc in got:
            A.tick()
            if (A.same(rc['source'], key) and A.same(rc['version'], ver)
                    and need <= rc['completed'].keys() and not need & rc['unknown']
                    and all(scope in rc['demands'] and A.same(demand, rc['demands'][scope][1])
                            for scope, demand in demands.items())):
                hit = True
                break
        if not hit:
            A.once('version_not_attested', (A.canon(key), A.canon(ver)),
                   f'{where}: required {key["producer"]} source version {ver["source_revision"]} has no '
                   'source_received completing its demanded scopes (further uses suppressed)')
    missing = set(adm['rs']) - set(adm['rv']) - set(adm['mv']) - set(adm['ms'])
    if missing:
        A.err('scope_without_version', f'{where}: {len(missing)} required scope source(s) lack a version')


def _check_admissions(A, by_event, caps, receipts):
    seen = {}
    for i, r in by_event.get('admitted', ()):
        A.counts['admitted'] += 1
        w = f'admitted record {i}'
        adm = _admission(A, r.get('admission'), w)
        c = caps.get(r.get('capture_attempt_id'))
        if (c is None or c.get('start') is None) and r.get('capture_attempt_id') is not None:
            A.err('admitted_unjoined', f'{w}: no capture_start')
        elif c is not None and c.get('start') is not None:
            s = c['start'][1]
            if any(s.get(f) != r.get(f) for f in ('thread_id', 'thread_birth', 'owner_token', 'feed_epoch')):
                A.err('admitted_unjoined', f'{w}: thread/owner/feed_epoch differs from capture_start')
            if not (s['monotonic_ns'] <= r['monotonic_ns']):
                A.err('admitted_clock', f'{w}: before capture_start')
            e = c.get('end')
            if e is not None and e[1]['monotonic_ns'] < r['monotonic_ns']:
                A.err('admitted_clock', f'{w}: after capture_end')
        if adm is None:
            continue
        if adm['id'] in seen and not _stable_equal(A, seen[adm['id']], adm):
            A.err('admission_identity_conflict', f'{w}: id {adm["id"]}')
        seen.setdefault(adm['id'], adm)
        _attest(A, adm, receipts, w, publication=False)
        for rid in adm['receipts']:
            rc = receipts.get(rid)
            if rc is not None and rc['rec'][1]['monotonic_ns'] > r['monotonic_ns']:
                A.err('admission_before_receipt', f'{w}: receipt {rid} received later')
    return seen


# ---------------------------------------------------------------- writers / projections

def _index_writers(A, by_event):
    w = defaultdict(dict)
    for ev in ('writer_request', 'writer_acquire', 'writer_release'):
        for i, r in by_event.get(ev, ()):
            wid = r.get('writer_id')
            if not _str(wid):
                A.err('writer_id_missing', f'{ev} record {i}'); continue
            if ev in w[wid]:
                A.err('duplicate_writer_record', f'{ev} {wid}'); continue
            w[wid][ev] = (i, r)
    return w


def _writer_triple(A, wid, writers, pub, where, model_mono, thread):
    """Check request/acquire/release for the writer the publication names; return (acq, rel) or None."""
    w = writers.get(wid)
    if not w or len(w) < 3:
        have = ','.join(sorted(w or {})) or 'none'
        A.err('writer_chain_incomplete', f'{where}: writer {wid} has {have}; needs request+acquire+release')
        return None
    rq, ac, rl = (w['writer_request'][1], w['writer_acquire'][1], w['writer_release'][1])
    ok = True
    for rec, n in ((rq, 'request'), (ac, 'acquire'), (rl, 'release')):
        if rec.get('thread_id') != thread[0] or rec.get('thread_birth') != thread[1]:
            A.err('writer_thread_mismatch', f'{where}: writer {n} thread differs from publication thread'); ok = False
        if rec.get('feed_epoch') != pub['feed_epoch']:
            A.err('writer_feed_epoch_mismatch', f'{where}: writer {n} feed_epoch'); ok = False
        if rec.get('producer_kind') != PRODUCER or rec.get('producer_kind') != pub['writer'].get('producer_kind'):
            A.err('writer_producer_mismatch', f'{where}: writer {n} producer_kind'); ok = False
        if rec.get('requested_ns') != pub['writer'].get('requested_ns') or not _int(rec.get('requested_ns')):
            A.err('writer_clock_mismatch', f'{where}: writer {n} requested_ns'); ok = False
    if rl.get('end_feed_epoch') != pub['feed_epoch']:
        A.err('writer_feed_epoch_mismatch', f'{where}: release end_feed_epoch (epoch changed during hold)'); ok = False
    if rl.get('acquired') is not True:
        A.err('writer_not_acquired', f'{where}: release.acquired is not true'); ok = False
    a_ns = pub['writer'].get('acquired_ns')
    if not (_int(a_ns) and ac.get('acquired_ns') == a_ns and rl.get('acquired_ns') == a_ns):
        A.err('writer_clock_mismatch', f'{where}: acquired_ns differs across publication/acquire/release'); ok = False
    r_ns = rl.get('released_ns')
    if not (_int(r_ns) and _int(a_ns) and rq['requested_ns'] <= a_ns <= model_mono <= r_ns):
        A.err('writer_clock_order', f'{where}: need requested<=acquired<=model_stored<=released'); ok = False
    if ok and not (rq['monotonic_ns'] >= rq['requested_ns'] and ac['monotonic_ns'] >= a_ns
                   and rl['monotonic_ns'] >= r_ns and ac['monotonic_ns'] <= rl['monotonic_ns']):
        A.err('writer_clock_order', f'{where}: record clocks precede their own timestamps'); ok = False
    return (a_ns, r_ns) if ok else None


def _index_projections(A, by_event):
    pr = {}
    for ev in ('projection_start', 'projection_end'):
        for i, r in by_event.get(ev, ()):
            pid = r.get('projection_attempt_id')
            if not _str(pid):
                A.err('projection_attempt_missing', f'{ev} record {i}'); continue
            d = pr.setdefault(pid, {})
            if ev in d:
                A.err('duplicate_projection_record', f'{ev} {pid}'); continue
            d[ev] = (i, r)
    return pr


def _check_projection(A, pid, d, adm_by_id):
    """Shape + start/end join for one successful attempt; return parsed dict or None."""
    st, en = d.get('projection_start'), d.get('projection_end')
    w = f'projection {pid}'
    if st is None:
        A.err('projection_start_missing', w); return None
    if en is None:
        A.err('projection_end_missing', w); return None
    s, e = st[1], en[1]
    ok = True
    for f in ('covered_scopes', 'missing_scopes', 'dependencies'):
        if f not in e:
            A.err('projection_end_field_missing', f'{w}: {f}'); ok = False
    if 'pinned_versions' not in s:
        A.err('projection_start_field_missing', f'{w}: pinned_versions'); ok = False
    if not ok:
        return None
    for f in ('thread_id', 'thread_birth', 'feed_epoch', 'coordinator_epoch'):
        if s.get(f) != e.get(f) or s.get(f) is None:
            A.err('projection_join_mismatch', f'{w}: {f} differs between start and end'); ok = False
    if not A.same(s.get('admission'), e.get('admission')):
        A.err('projection_join_mismatch', f'{w}: admission differs between start and end'); ok = False
    if s['monotonic_ns'] > e['monotonic_ns']:
        A.err('projection_clock', f'{w}: end before start'); ok = False
    adm = _admission(A, e.get('admission'), w)
    if adm is None:
        return None
    pv = A.entries(s['pinned_versions'], w + ' pinned_versions', allow_empty_dict=False)
    cs = A.entries(e['covered_scopes'], w + ' covered_scopes')
    ms = A.entries(e['missing_scopes'], w + ' missing_scopes')
    dp = A.entries(e['dependencies'], w + ' dependencies')
    if None in (pv, cs, ms, dp) or not ok:
        return None
    if not A.same(s['pinned_versions'], adm['a']['required_versions']):
        A.err('projection_version_mismatch', f'{w}: pinned_versions != admission.required_versions'); ok = False
    if not A.same(e['missing_scopes'], adm['a']['missing_scopes']):
        A.err('projection_scope_mismatch', f'{w}: missing_scopes != admission.missing_scopes'); ok = False
    # covered + missing must account exactly for the admission's required scope sources
    want = {k: A.canon(v) for k, (kk, v) in adm['rs'].items()}
    got = {k: A.canon(v) for k, (kk, v) in cs.items()}
    absent = set(ms) | set(adm['ms'])
    if set(got) | absent != set(want) or set(got) & absent or any(got[k] != want[k] for k in got):
        A.err('projection_scope_mismatch', f'{w}: covered_scopes/missing_scopes do not partition required_scopes'); ok = False
    if set(dp) != (set(got) | absent):
        A.err('projection_dependency_mismatch', f'{w}: dependency sources must cover every demanded scope'); ok = False
    for k, (kk, v) in dp.items():
        if not isinstance(v, list):
            A.err('projection_dependency_mismatch', f'{w}: dependency value not a list'); ok = False; break
    admv = adm_by_id.get(adm['id'])
    if admv is not None and not _stable_equal(A, admv, adm):
        A.err('admission_identity_conflict', f'{w}: identity differs from admitted record'); ok = False
    if admv is None:
        A.err('projection_admission_missing', w); ok = False
    if not _int(adm['a']['first_admitted_mono_ns']) or adm['a']['first_admitted_mono_ns'] > s['monotonic_ns']:
        A.err('projection_clock', f'{w}: admission first_admitted_mono_ns after projection_start'); ok = False
    for k, (kk, v) in pv.items():
        if not _source_key_ok(kk) or not _version_ok(v):
            A.err('projection_version_mismatch', f'{w}: pinned_versions entry incomplete'); return None
        if v['store_epoch_uuid'] != e['coordinator_epoch']:
            A.err('projection_version_mismatch', f'{w}: pinned version epoch != coordinator_epoch'); ok = False
    if not _str(e.get('session')) or s.get('chat') != adm['a']['chat'] or \
            e['session'] != adm['a']['chat'].get('session_id'):
        A.err('projection_join_mismatch', f'{w}: session/chat differs from admission'); ok = False
    return dict(start=st, end=en, adm=adm, ok=ok)


# ---------------------------------------------------------------- publications

PUB_FIELDS = ('admission', 'canonical_seq', 'chat', 'chat_rev', 'computed_at', 'computed_seq', 'covered_scopes',
              'dependencies', 'feed_epoch', 'missing_scopes', 'model_revision', 'publication_id',
              'session', 'source_vector', 'writer')


def _check_publications(A, by_event, caps, receipts, writers, projections):
    adm_seen = {}
    for i, r in by_event.get('admitted', ()):
        a = r.get('admission')
        if isinstance(a, dict) and _str(a.get('id')):
            ap = _parse_quiet(A, a)
            if ap is not None:
                adm_seen.setdefault(ap['id'], ap)
    models = by_event.get('model_stored', ())
    A.counts['publications'] = len(models)
    pubs, seen_ids = [], set()
    for i, r in models:
        w = f'model_stored record {i}'
        p = r.get('publication')
        if not isinstance(p, dict):
            A.err('publication_malformed', w); continue
        miss = [f for f in PUB_FIELDS if f not in p]
        if miss:
            A.err('publication_field_missing', f'{w}: {",".join(miss)}'); continue
        if not _str(p['publication_id']) or not _str(p['session']) or not _str(p['feed_epoch']) \
                or not _str(p['model_revision']) or not _int(p['canonical_seq']) or not _int(p['chat_rev']) \
                or not _int(p['computed_seq']) or not _num(p['computed_at']):
            A.err('publication_malformed', f'{w}: scalar field types'); continue
        if p['publication_id'] in seen_ids:
            A.err('duplicate_publication_id', p['publication_id']); continue
        seen_ids.add(p['publication_id'])
        _payload(A, r, w)
        adm = _admission(A, p['admission'], w)
        if adm is None:
            continue
        cs = A.entries(p['covered_scopes'], w + ' covered_scopes')
        ms = A.entries(p['missing_scopes'], w + ' missing_scopes')
        dp = A.entries(p['dependencies'], w + ' dependencies')
        sv = A.entries(p['source_vector'], w + ' source_vector', allow_empty_dict=False)
        if None in (cs, ms, dp, sv):
            continue
        ok = True
        if not A.same(p['chat'], adm['a']['chat']) or adm['a']['chat'].get('session_id') != p['session']:
            A.err('publication_admission_mismatch', f'{w}: chat/session differs from admission'); ok = False
        if not A.same(p['source_vector'], adm['a']['required_versions']):
            A.err('publication_admission_mismatch', f'{w}: source_vector != admission.required_versions'); ok = False
        if not A.same(p['missing_scopes'], adm['a']['missing_scopes']):
            A.err('publication_admission_mismatch', f'{w}: missing_scopes != admission.missing_scopes'); ok = False
        known = adm_seen.get(adm['id'])
        if known is not None and not _stable_equal(A, known, adm):
            A.err('admission_identity_conflict', f'{w}: differs from admitted record identity'); ok = False
        if adm['mv'] or adm['ms'] or p['missing_scopes'] != {}:
            A.counts['publications_with_missing_scopes'] += 1   # never authoritative absence
        _attest(A, adm, receipts, w, publication=True)
        pubs.append(dict(i=i, r=r, p=p, adm=adm, w=w, ok=ok))
    # writer chains
    spans = defaultdict(list)       # feed_epoch -> [(acquired, released, writer_id)]
    thread_pubs = defaultdict(list)
    for x in pubs:
        i, r, p, w = x['i'], x['r'], x['p'], x['w']
        wr = p['writer']
        thread = (r['thread_id'], r['thread_birth'])
        if wr is None:
            A.counts['publications_without_writer'] += 1
            A.err('publication_writer_missing', f'{w}: publication.writer is null; no request/acquire/release custody')
            x['span'] = None
        elif not (isinstance(wr, dict) and _str(wr.get('writer_id')) and _int(wr.get('acquired_ns'))
                  and _int(wr.get('requested_ns')) and _str(wr.get('feed_epoch'))
                  and _str(wr.get('producer_kind'))):
            A.err('publication_writer_malformed', w); x['span'] = None
        elif wr.get('feed_epoch') != p['feed_epoch']:
            A.err('writer_feed_epoch_mismatch', f'{w}: publication.writer.feed_epoch'); x['span'] = None
        else:
            x['span'] = _writer_triple(A, wr['writer_id'], writers, p, w, r['monotonic_ns'],
                                       thread)
            if x['span'] is not None:
                x['wid'] = wr['writer_id']
                spans[p['feed_epoch']].append((x['span'][0], x['span'][1], wr['writer_id']))
        thread_pubs[(thread, p['admission']['id'], p['session'])].append(x)
    # serialized hold: distinct writers never overlap within a feed epoch
    for ep, lst in spans.items():
        uniq = sorted(set(lst))
        for a, b in zip(uniq, uniq[1:]):
            A.tick()
            if b[0] < a[1]:
                A.err('writer_overlap', f'feed_epoch {ep}: writers {a[2][:8]} and {b[2][:8]} overlap')
    # Every serialized publication names its immutable projection attempt.
    # Other-chat wire carriage keeps that attempt; a fresh retry gets a fresh id.
    # Metadata similarity can never substitute for a removed attempt's records.
    checked, direct = {}, defaultdict(list)
    for pid, d in projections.items():
        en = d.get('projection_end')
        if en is None or en[1].get('disposition') not in SUCCESS_DISPOSITIONS:
            continue
        A.counts['projection_success'] += 1
        pj = _check_projection(A, pid, d, adm_seen)
        if pj is None or not A.tick():
            continue
        checked[pid] = pj
        s, e = pj['start'][1], pj['end'][1]
        for rid in pj['adm']['receipts']:
            rc = receipts.get(rid)
            if rc is not None and rc['rec'][1]['monotonic_ns'] > s['monotonic_ns']:
                A.err('projection_before_receipt', pid)
    for x in sorted(pubs, key=lambda x: x['r']['monotonic_ns']):
        pid = x['p'].get('projection_attempt_id')
        pj = checked.get(pid) if isinstance(pid, str) else None
        if pj is None or not pj['ok']:
            A.err('publication_without_projection', x['w']); continue
        s, e = pj['start'][1], pj['end'][1]
        span = x.get('span')
        own = (span is not None and s['thread_id'] == x['r']['thread_id']
               and s['thread_birth'] == x['r']['thread_birth']
               and s['monotonic_ns'] <= x['p']['writer']['requested_ns']
               and span[1] <= e['monotonic_ns'])
        if own:
            direct[pid].append(x)
            if len(direct[pid]) > 1:
                A.err('projection_multiple_publications', pid)
        elif not direct[pid] or direct[pid][0]['r']['monotonic_ns'] > x['r']['monotonic_ns']:
            A.err('publication_carriage_without_prior', x['w']); continue
        else:
            A.counts['publications_exact_projection_carriage'] += 1
        _join_projection(A, pid, pj, x, carried=not own)
    for pid, pj in checked.items():
        if pj['end'][1]['disposition'] == 'stored' and not direct[pid]:
            A.err('projection_without_publication', pid)
    A.counts['publications_joined_projection'] = sum(map(len, direct.values()))


def _parse_quiet(A, a):
    """Admission parse that does not double-report (admitted records already audited)."""
    saved = (list(A.errors), Counter(A.codes))
    out = _admission(A, a, 'admitted')
    A.errors[:], A.codes = saved[0], saved[1]
    return out


def _payload(A, r, w):
    b, h, n = r.get('payload_b64'), r.get('payload_sha256'), r.get('serialized_bytes')
    if not (isinstance(b, str) and _str(h) and _int(n)):
        A.err('payload_incomplete', w); return
    try:
        raw = base64.b64decode(b, validate=True)
    except ValueError:
        A.err('payload_incomplete', f'{w}: base64'); return
    if hashlib.sha256(raw).hexdigest() != h or len(raw) != n:
        A.err('payload_mismatch', f'{w}: sha256/serialized_bytes')


def _join_projection(A, pid, pj, x, *, carried=False):
    p, r, w = x['p'], x['r'], x['w'] + f' <- projection {pid}'
    s, e = pj['start'][1], pj['end'][1]
    for f in ('feed_epoch',):
        if p[f] != e.get(f):
            A.err('projection_publication_mismatch', f'{w}: {f}')
    if not A.same(p['admission'], e['admission']):
        A.err('projection_publication_mismatch', f'{w}: admission differs from projection')
    if not A.same(p['source_vector'], s['pinned_versions']):
        A.err('projection_publication_mismatch', f'{w}: source_vector != projection_start.pinned_versions')
    for f in ('covered_scopes', 'missing_scopes', 'dependencies'):
        if not A.same(p[f], e[f]):
            A.err('projection_publication_mismatch', f'{w}: {f} differs from projection_end')
    if p['session'] != e['session'] or not A.same(p['chat'], s.get('chat')):
        A.err('projection_publication_mismatch', f'{w}: session/chat')
    sp = x.get('span')
    if sp is not None and not carried:
        if not (s['monotonic_ns'] <= p['writer']['requested_ns'] and sp[1] <= e['monotonic_ns']):
            A.err('writer_clock_order', f'{w}: writer hold must lie inside projection start..end')
        if e.get('thread_id') != r['thread_id']:
            A.err('writer_thread_mismatch', w)
    A.counts['publications_audited'] += 1


# ---------------------------------------------------------------- emits / diagnostics

def _check_emits(A, by_event, writers):
    stored = {}
    for i, r in by_event.get('model_stored', ()):
        p = r.get('publication')
        if isinstance(p, dict) and _str(p.get('publication_id')):
            stored[p['publication_id']] = (i, r)
    for i, r in by_event.get('emit_success', ()):
        A.counts['emit_success'] += 1
        w = f'emit_success record {i}'
        if r.get('observation') != 'server_emission':
            A.err('emit_malformed', f'{w}: observation'); continue
        if r.get('kind') != 'model':
            continue                    # invalidation_only emits fulfil no model publication
        pubs = r.get('publications')
        if not isinstance(pubs, list) or not pubs:
            A.err('emit_malformed', f'{w}: model emit without publications'); continue
        s, c = r.get('callback_started_ns'), r.get('callback_completed_ns')
        if not (_int(s) and _int(c) and s <= c <= r['monotonic_ns']):
            A.err('emit_malformed', f'{w}: callback clocks'); continue
        _payload(A, r, w)
        for p in pubs:
            pid = p.get('publication_id') if isinstance(p, dict) else None
            m = stored.get(pid)
            if m is None:
                A.err('emit_without_model_stored', f'{w}: publication {pid}'); continue
            if not A.same(p, m[1]['publication']):
                A.err('emit_publication_mismatch', f'{w}: publication {pid} differs from stored'); continue
            if (m[1]['thread_id'], m[1]['thread_birth']) != (r['thread_id'], r['thread_birth']):
                A.err('emit_thread_mismatch', f'{w}: publication {pid}')
            wr = m[1]['publication'].get('writer')
            wid = wr.get('writer_id') if isinstance(wr, dict) else None
            rl = writers.get(wid, {}).get('writer_release')
            if rl is not None and not (m[1]['monotonic_ns'] <= s and c <= rl[1].get('released_ns', -1)):
                A.err('emit_clock_order', f'{w}: callback must lie between model_stored and writer release')
            A.counts['publications_emitted'] += 1


def _count_diagnostics(A, by_event, caps, projections, writers):
    """Censored/unfulfilled records: counted, never errors, never success."""
    A.counts['capture_unfinished'] = sum(1 for c in caps.values() if c.get('start') and not c.get('end'))
    A.counts['capture_failed'] = sum(1 for c in caps.values()
                                     if c.get('end') and c['end'][1].get('disposition') not in RECEIVED_OK)
    A.counts['projection_unfinished'] = sum(1 for d in projections.values()
                                            if 'projection_start' in d and 'projection_end' not in d)
    A.counts['projection_not_success'] = sum(1 for d in projections.values() if 'projection_end' in d
                                             and d['projection_end'][1].get('disposition') not in SUCCESS_DISPOSITIONS)
    A.counts['writer_unfulfilled'] = sum(1 for w in writers.values() if len(w) < 3)
    stored_ids = {r['publication'].get('publication_id') for _, r in by_event.get('model_stored', ())
                  if isinstance(r.get('publication'), dict)}
    emitted = set()
    for _, r in by_event.get('emit_success', ()):
        for p in r.get('publications') or ():
            if isinstance(p, dict):
                emitted.add(p.get('publication_id'))
    A.counts['stored_not_emitted'] = len(stored_ids - emitted)
    A.counts['source_error'] = len(by_event.get('source_error', ()))
    A.counts['emit_error'] = len(by_event.get('emit_error', ()))
    # writer pieces that no publication or complete chain explains
    for wid, w in writers.items():
        if 'writer_request' not in w:
            A.err('writer_orphan', f'writer {wid} has {",".join(sorted(w))} without writer_request')
