"""Bounded pure analyzer for private panel-source-receipts/1 stage latency/accounting.

Pure: reads already-parsed record dicts, never opens files/sockets, never activates
anything and is not a receipt writer. Inclusion acceptance is delegated to the real
``source_receipts.validate_records`` (loaded as a sibling module when this file is
imported outside the package); it is never re-implemented or weakened here.

Honest limits: timings alone never produce an acceptance verdict. The semantic
expected-row/action/absence oracle, the native Desktop p95 and CPU gates are
reported ``NOT_EVALUATED``. Missing evidence is reported as ``None``/explicit
unavailable counters, never as inferred zeros.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import math
import sys
from collections import Counter
from pathlib import Path

NOT_EVALUATED = 'NOT_EVALUATED'
BUDGET_NS = 15_000_000_000
EMIT, CATCHUP = 'server_emission', 'authenticated_catchup'
IDENTITY_FIELDS = ('boot_id', 'pid', 'process_birth_ticks', 'runtime_sha', 'mods_sha',
                   'core_sha', 'api_generation', 'serving_gateway', 'profile')
MAX_RECORDS, MAX_INCLUSION_CHECKS, MAX_WINDOWS = 200_000, 5_000, 64
MAX_LIST, MAX_STR, SAMPLE = 4096, 512, 50
MAX_INPUT_BYTES, MAX_LINE_BYTES = 256 * 1024 * 1024, 12 * 1024 * 1024
MAX_NODES, MAX_DEPTH = 2_000_000, 32
MAX_JOIN_WORK = 2_000_000


def _bound_content(records):
    """Bound detached JSON work before validator hashing/decoding or recursion."""
    stack = [(records, 0)]
    nodes = size = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise AnalysisBounds('input structural bound exceeded')
        if isinstance(value, (dict, list, tuple)):
            if len(value) > MAX_RECORDS:
                raise AnalysisBounds('input collection bound exceeded')
            children = list(value.items()) if isinstance(value, dict) else value
            stack.extend((child, depth + 1) for child in children)
        elif isinstance(value, str):
            size += len(value.encode('utf-8'))
            if size > MAX_INPUT_BYTES:
                raise AnalysisBounds('input byte bound exceeded')
        elif value is not None and type(value) not in (int, float, bool):
            raise AnalysisBounds('non JSON input')


class AnalysisBounds(ValueError):
    pass


def load_receipts(path=None):
    """Real source_receipts module: package-relative if packaged, else sibling file."""
    if path is None and __package__:
        from . import source_receipts
        return source_receipts
    path = Path(path) if path else Path(__file__).with_name('source_receipts.py')
    name = '_panel_source_receipts_analysis'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(str(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def clock(v):
    """Valid monotonic/ns clock: real int, not bool, non-negative. Floats are refused."""
    return type(v) is int and v >= 0


def _s(v):
    return isinstance(v, str) and 0 < len(v) <= MAX_STR


def pairs(v):
    """Decode detached maps: plain dict or {'entries': [[k, v], ...]}; None if malformed."""
    if isinstance(v, dict):
        if set(v) == {'entries'}:
            e = v['entries']
            if (isinstance(e, list) and len(e) <= MAX_LIST
                    and all(isinstance(p, list) and len(p) == 2 for p in e)):
                return [tuple(p) for p in e]
            return None
        return list(v.items()) if len(v) <= MAX_LIST else None
    return None


def chat_key(v):
    if (isinstance(v, dict) and all(_s(v.get(k)) for k in ('serving_gateway', 'profile', 'session_id'))):
        return (v['serving_gateway'], v['profile'], v['session_id'])
    return None


def _canon(v):
    return json.dumps(v, sort_keys=True, separators=(',', ':'), allow_nan=False, ensure_ascii=False)


def parse_admission(a):
    """Detached Admission observation; vectors may evolve under the same stable id."""
    if not isinstance(a, dict) or not _s(a.get('id')):
        return None, 'admission id'
    chat = chat_key(a.get('chat'))
    if chat is None:
        return None, 'admission chat'
    if not clock(a.get('first_admitted_mono_ns')):
        return None, 'admission first_admitted_mono_ns clock'
    ids = a.get('receipt_ids')
    if not isinstance(ids, list) or len(ids) > MAX_LIST or not all(_s(i) for i in ids):
        return None, 'admission receipt_ids'
    if pairs(a.get('required_versions')) is None or pairs(a.get('required_scopes')) is None:
        return None, 'admission required_versions/required_scopes'
    stable = dict(id=a['id'], chat=chat, first_admitted_mono_ns=a['first_admitted_mono_ns'])
    # Legacy fixture omissions are unknown, not invented tokens/wall clocks. A
    # later present observation can supply them; two known values must agree.
    for field in ('watch_token', 'first_admitted_wall_ns'):
        if field in a:
            if not (_s(a[field]) if field == 'watch_token' else clock(a[field])):
                return None, 'admission ' + field
            stable[field] = a[field]
    return dict(id=a['id'], chat=chat, first=a['first_admitted_mono_ns'],
                receipt_ids=tuple(dict.fromkeys(ids)), stable=stable), None


def stats(values, *, denominator=None, unit='ns'):
    """Nearest-rank p95 (ceil(0.95*n)-th smallest), denominator and max."""
    v = sorted(values)
    n = len(v)
    return {'denominator': n if denominator is None else denominator, 'samples': n,
            'p95_ns': v[math.ceil(0.95 * n) - 1] if n else None, 'max_ns': v[-1] if n else None}


def key_of(p):
    fields = tuple(p.get(k) for k in ('feed_epoch', 'publication_id', 'session', 'model_revision'))
    return fields if all(_s(v) for v in fields) else None


class _Ctx:
    def __init__(self, receipts, budget_ns):
        self.R, self.budget = receipts, budget_ns
        self.rejected, self.reject_reasons = 0, Counter()
        self.samples = []

    def reject(self, why, rec=None):
        self.rejected += 1
        self.reject_reasons[why] += 1
        if len(self.samples) < SAMPLE:
            self.samples.append({'reason': why, 'record_id': rec.get('record_id') if isinstance(rec, dict) else None})


def _screen(r, ctx):
    """Common custody shape; returns window id or None (record excluded, counted)."""
    if (not isinstance(r, dict) or r.get('schema') != ctx.R.SCHEMA
            or not isinstance(r.get('event'), str) or r['event'] not in ctx.R.EVENTS):
        ctx.reject('schema/event', r); return None
    if not _s(r.get('window_id')) or not _s(r.get('record_id')):
        ctx.reject('window/record identity', r); return None
    if (any(not _s(r.get(k)) for k in IDENTITY_FIELDS if k not in ('pid', 'process_birth_ticks'))
            or any(type(r.get(k)) is not int or r[k] <= 0 for k in ('pid', 'process_birth_ticks'))):
        ctx.reject('runtime identity', r); return None
    if not clock(r.get('monotonic_ns')) or not clock(r.get('wall_ns')):
        ctx.reject('record clock', r); return None
    return r['window_id']


def analyze_records(records, *, receipts=None, budget_ns=BUDGET_NS, require_closed=True,
                    expected_identity=None, max_records=MAX_RECORDS,
                    max_inclusion_checks=MAX_INCLUSION_CHECKS):
    """Analyze one measurement manifest. Never raises on adversarial content except bounds."""
    if not isinstance(records, (list, tuple)):
        raise AnalysisBounds('records must be a list')
    if len(records) > max_records:
        raise AnalysisBounds(f'record count exceeds {max_records}')
    _bound_content(records)
    for record in records:
        if isinstance(record, dict) and isinstance(record.get('payload_b64'), str):
            try:
                body = json.loads(base64.b64decode(record['payload_b64'], validate=True))
            except RecursionError as exc:
                raise AnalysisBounds('embedded payload recursion bound exceeded') from exc
            except (ValueError, TypeError):
                continue  # the exact inclusion validator reports corrupt payloads
            _bound_content(body)
    R = receipts or load_receipts()
    ctx = _Ctx(R, budget_ns)

    # ---- screen + dedupe (identical duplicate observations are never pooled) ----
    recs, seen, dup_identical, dup_conflict = [], {}, 0, 0
    poisoned_ids = set()
    windows = {}
    for r in records:
        w = _screen(r, ctx)
        if w is None:
            continue
        ident = tuple(r[k] for k in IDENTITY_FIELDS)
        if expected_identity is not None and any(r.get(k) != v for k, v in expected_identity.items()):
            ctx.reject('identity differs from expected runtime', r); continue
        if windows.setdefault(w, ident) != ident:
            ctx.reject('runtime identity changed within window', r); continue
        if len(windows) > MAX_WINDOWS:
            raise AnalysisBounds('too many windows')
        rid = r['record_id']
        if rid in seen:
            if _canon_safe(seen[rid]) == _canon_safe(r):
                dup_identical += 1
            else:
                dup_conflict += 1
                poisoned_ids.add(rid)
            continue
        seen[rid] = r
        recs.append(r)

    recs = [r for r in recs if r['record_id'] not in poisoned_ids]
    validator = R.validate_records(recs, require_closed=require_closed)

    # ---- windows / log gaps (explicit, never inferred zero) ----
    win = {w: dict(closed=False, last_mono=0, end_mono=None, dropped_records=None, write_errors=None,
                   encoding_errors=None, records_after_end=0) for w in windows}
    for r in recs:
        s = win[r['window_id']]
        if s['closed']:
            s['records_after_end'] += 1
            continue
        s['last_mono'] = max(s['last_mono'], r['monotonic_ns'])
        if r['event'] == 'window_end':
            s['closed'], s['end_mono'] = True, r['monotonic_ns']
            for k in ('dropped_records', 'write_errors', 'encoding_errors'):
                s[k] = r.get(k)
    def obs_end(w):
        s = win[w]
        return s['end_mono'] if s['closed'] else s['last_mono']
    usable, closed = [], set()
    for r in recs:
        w = r['window_id']
        if w in closed or r['monotonic_ns'] > obs_end(w):
            ctx.reject('record outside terminal window', r)
            continue
        usable.append(r)
        if r['event'] == 'window_end':
            closed.add(w)
    recs = usable
    lossy = {w for w, s in win.items() if s['closed'] and (
        type(s['dropped_records']) is not int or s['dropped_records'] != 0
        or s['write_errors'] != [] or s['encoding_errors'] != [])}
    unclosed = sorted(w for w, s in win.items() if not s['closed'])
    log_gaps = dict(windows=len(win), unclosed_windows=unclosed, lossy_windows=sorted(lossy),
        counters_complete=not unclosed and bool(win),
        dropped_records=sum(s['dropped_records'] for s in win.values() if type(s['dropped_records']) is int)
                        if not unclosed and win else None,
        per_window={w: {k: s[k] for k in ('closed', 'dropped_records', 'write_errors', 'encoding_errors',
                                           'records_after_end')} for w, s in win.items()})

    # ---- admissions (stable identity; coalesced vectors/receipt custody evolve) ----
    adm, integrity = {}, []
    adm_obs = Counter()
    admission_missing = Counter()
    for r in recs:
        if r['event'] != 'admitted':
            continue
        a, why = parse_admission(r.get('admission'))
        if a is None or chat_key(r.get('chat')) != a['chat']:
            ctx.reject('admission shape: ' + (why or 'chat mismatch'), r); continue
        for field in ('watch_token', 'first_admitted_wall_ns'):
            admission_missing[field] += field not in a['stable']
        k = (r['window_id'], a['id'])
        adm_obs[k] += 1
        if k in adm:
            old = adm[k]
            if any(old['stable'][f] != v for f, v in a['stable'].items() if f in old['stable']):
                integrity.append(f'conflicting stable admission {a["id"]}')
                old['conflict'] = True
            old['stable'].update(a['stable'])
            ids = tuple(dict.fromkeys(old['receipt_ids'] + a['receipt_ids']))
            if len(ids) > MAX_LIST:
                raise AnalysisBounds('accumulated admission receipt_ids exceeds bound')
            old['receipt_ids'] = ids
            continue
        adm[k] = dict(a, window=r['window_id'], conflict=False)
    for k in [k for k, a in adm.items() if a['conflict']]:
        del adm[k]  # a true stable-identity mutation is never evidence

    # ---- source receipts (receipt id == coverage.capture_id) ----
    source_recv = {}
    for r in recs:
        if r['event'] == 'source_received':
            rc = r.get('receipt')
            cov = rc.get('coverage') if isinstance(rc, dict) else None
            cid = cov.get('capture_id') if isinstance(cov, dict) else None
            if _s(cid) and isinstance(rc, dict) and clock(rc.get('received_mono_ns')):
                source_recv.setdefault((r['window_id'], cid), rc['received_mono_ns'])
            else:
                ctx.reject('source_received receipt/capture_id/clock', r)

    # ---- projections (first start per admitted receipt) ----
    proj_first = {}
    for r in recs:
        if r['event'] == 'projection_start':
            a, _ = parse_admission(r.get('admission'))
            if a is None:
                continue
            for rid in a['receipt_ids']:
                key = (r['window_id'], a['chat'], rid)
                if key not in proj_first or r['monotonic_ns'] < proj_first[key]:
                    proj_first[key] = r['monotonic_ns']

    # ---- supersession: explicit predecessor->successor admission ids only ----
    succ = {}
    for r in recs:
        if r['event'] == 'superseded':
            pa = r.get('admission')
            p = r.get('predecessor_admission_id') or (pa.get('id') if isinstance(pa, dict) else None)
            n = r.get('successor_admission_id') or ((r.get('successor') or {}).get('id') if isinstance(r.get('successor'), dict) else None)
            if _s(p) and _s(n):
                left, right = adm.get((r['window_id'], p)), adm.get((r['window_id'], n))
                if left is None or right is None or left['chat'] != right['chat']:
                    ctx.reject('supersession lacks same-chat admitted endpoints', r)
                    continue
                succ.setdefault((r['window_id'], p), set()).add(n)

    # ---- inclusion: ONLY through the real validator, per inclusion record ----
    stored_by_key = {}
    for r in recs:
        if r['event'] == 'model_stored' and isinstance(r.get('publication'), dict):
            k = key_of(r['publication'])
            if k is None:
                ctx.reject('invalid stored publication key', r)
                continue
            stored_by_key.setdefault(k, []).append(r)
    fulfil = []   # dict(window, chat, adm_id, receipt_ids, time, via, key)
    checks, rejected_inclusions, inclusion_obs = 0, 0, Counter()
    for r in recs:
        if r['event'] not in ('emit_success', 'catchup_success') or r.get('kind') != 'model':
            continue
        via = EMIT if r['event'] == 'emit_success' else CATCHUP
        t = r.get('callback_completed_ns') if via == EMIT else r.get('write_completed_ns')
        pubs = r.get('publications')
        if (not clock(t) or t > obs_end(r['window_id']) or t > r['monotonic_ns']
                or not isinstance(pubs, list) or len(pubs) > MAX_LIST):
            rejected_inclusions += 1; ctx.reject('inclusion clock/publications shape', r); continue
        if r.get('callback_started_ns') is not None and via == EMIT and not clock(r.get('callback_started_ns')):
            rejected_inclusions += 1; ctx.reject('inclusion start clock', r); continue
        checks += 1
        if checks > max_inclusion_checks:
            raise AnalysisBounds(f'inclusion checks exceed {max_inclusion_checks}')
        keys = [key_of(p) for p in pubs if isinstance(p, dict)]
        baseline = [s for k in set(keys) for s in stored_by_key.get(k, [])]
        if any(s['monotonic_ns'] > t for s in baseline):
            rejected_inclusions += 1; ctx.reject('inclusion before model storage', r); continue
        base = R.validate_records(baseline)
        res = R.validate_records(baseline + [r])
        if len(res['errors']) != len(base['errors']):
            rejected_inclusions += 1; ctx.reject('inclusion rejected by validator', r); continue
        newly = {tuple(k) for k in base['unmatched']} - {tuple(k) for k in res['unmatched']}
        for p in pubs:
            if not isinstance(p, dict) or key_of(p) not in newly:
                continue
            a, _ = parse_admission(p.get('admission'))
            c = chat_key(p.get('chat'))
            if a is None or c is None or c != a['chat']:
                ctx.reject('fulfilled publication lacks exact admission/chat', r); continue
            fulfil.append(dict(window=r['window_id'], chat=c, adm_id=a['id'], receipt_ids=set(a['receipt_ids']),
                               time=t, via=via, key=key_of(p), first=a['first']))
            inclusion_obs[(r['window_id'], key_of(p), via)] += 1
    fulfil_by = {}
    for f in fulfil:
        fulfil_by.setdefault((f['window'], f['chat']), []).append(f)

    # ---- event level: (window, chat, receipt id) with first admission preserved ----
    events = {}
    for (w, _), a in adm.items():
        for rid in a['receipt_ids']:
            e = events.setdefault((w, a['chat'], rid), dict(first=a['first'], adm=set()))
            e['first'] = min(e['first'], a['first'])
            e['adm'].add(a['id'])
    anomalies = Counter()
    join_work = 0
    for (w, chat, rid), e in events.items():
        e['via'] = {}
        for f in fulfil_by.get((w, chat), ()):
            join_work += 1
            if join_work > MAX_JOIN_WORK:
                raise AnalysisBounds('event inclusion join bound exceeded')
            ok = rid in f['receipt_ids']
            if not ok:  # explicit declared successor (transitive, bounded)
                frontier, hops = set(e['adm']), 0
                while frontier and hops < 8 and not ok:
                    join_work += len(frontier) + sum(len(succ.get((w, x), ())) for x in frontier)
                    if join_work > MAX_JOIN_WORK:
                        raise AnalysisBounds('supersession traversal bound exceeded')
                    nxt = set().union(*(succ.get((w, x), set()) for x in frontier))
                    ok = f['adm_id'] in nxt
                    frontier, hops = nxt, hops + 1
            if not ok:
                continue
            if f['time'] < e['first']:
                anomalies['inclusion_before_first_admission'] += 1; continue
            if f['via'] not in e['via'] or f['time'] < e['via'][f['via']]:
                e['via'][f['via']] = f['time']
        e['ful'] = min(e['via'].values()) if e['via'] else None
        e['ful_via'] = min(e['via'], key=e['via'].get) if e['via'] else None

    def lb(w, first):
        v = obs_end(w) - first
        if v < 0:
            anomalies['observation_end_before_first_admission'] += 1
            return None
        return v

    ev_lat, ev_unf = [], []
    by_via = {EMIT: [], CATCHUP: []}
    late = 0
    for (w, chat, rid), e in events.items():
        if e['ful'] is not None:
            ev_lat.append(e['ful'] - e['first'])
            late += (e['ful'] - e['first']) > budget_ns
            for via, t in e['via'].items():
                by_via[via].append(t - e['first'])
        else:
            ev_unf.append(dict(window=w, chat=chat, receipt_id=rid, lower_bound_ns=lb(w, e['first']),
                               window_closed=win[w]['closed']))
    unf_bounds = [u['lower_bound_ns'] for u in ev_unf if u['lower_bound_ns'] is not None]
    event_report = dict(
        total=len(events), fulfilled=len(ev_lat), unfulfilled=len(ev_unf),
        fulfilled_series=stats(ev_lat, denominator=len(events)),
        by_observation={EMIT: stats(by_via[EMIT], denominator=len(events)),
                        CATCHUP: stats(by_via[CATCHUP], denominator=len(events))},
        fulfilled_over_budget=late,
        unfulfilled_lower_bound_over_budget=sum(b > budget_ns for b in unf_bounds),
        unfulfilled_lower_bound_unknown=len(ev_unf) - len(unf_bounds),
        p95_lower_bound_including_unfulfilled_ns=stats(ev_lat + unf_bounds)['p95_ns'],
        unfulfilled_sample=ev_unf[:SAMPLE])

    # ---- per-chat first-admission episodes (primary gate shape) ----
    per_chat = {}
    for (w, chat, rid), e in events.items():
        per_chat.setdefault((w, chat), []).append(dict(e, rid=rid))
    episodes = []
    for (w, chat), evs in per_chat.items():
        rem = sorted(evs, key=lambda e: (e['first'], e['rid']))
        while rem:
            e0 = rem[0]
            f0 = e0['ful']
            if f0 is None:
                members, rem = rem, []
                episodes.append(dict(window=w, chat=chat, start=e0['first'], events=len(members), fulfilled=False,
                                     lower_bound_ns=lb(w, e0['first']), via=None, window_closed=win[w]['closed']))
                continue
            members = [e for e in rem if e['first'] <= f0]
            rem = [e for e in rem if not (e['first'] <= f0 and e['ful'] is not None and e['ful'] <= f0)]
            episodes.append(dict(window=w, chat=chat, start=e0['first'], events=len(members), fulfilled=True,
                                 latency_ns=f0 - e0['first'], via=e0['ful_via']))
    ful_ep = [e for e in episodes if e['fulfilled']]
    unf_ep = [e for e in episodes if not e['fulfilled']]
    ep_bounds = [e['lower_bound_ns'] for e in unf_ep if e['lower_bound_ns'] is not None]
    lat = [e['latency_ns'] for e in ful_ep]
    episode_report = dict(
        total=len(episodes), fulfilled=len(ful_ep), unmatched=len(unf_ep),
        fulfilled_series=stats(lat, denominator=len(episodes)),
        by_fulfilling_observation={v: stats([e['latency_ns'] for e in ful_ep if e['via'] == v],
                                            denominator=len(episodes)) for v in (EMIT, CATCHUP)},
        coalesced_episodes=sum(e['events'] > 1 for e in episodes),
        fulfilled_over_budget=sum(x > budget_ns for x in lat),
        unmatched_right_censored=sum(not e['window_closed'] for e in unf_ep),
        unmatched_lower_bound_over_budget=sum(b > budget_ns for b in ep_bounds),
        unmatched_lower_bound_unknown=len(unf_ep) - len(ep_bounds),
        p95_lower_bound_including_unmatched_ns=stats(lat + ep_bounds)['p95_ns'],
        budget_ns=budget_ns,
        unmatched_sample=[{k: v for k, v in e.items() if k != 'via'} for e in unf_ep[:SAMPLE]])
    failures_over_budget = episode_report['fulfilled_over_budget'] + episode_report['unmatched_lower_bound_over_budget']

    # ---- separate stage series ----
    s2a, a2p, miss_src, miss_proj = [], [], 0, 0
    for (w, chat, rid), e in events.items():
        t = source_recv.get((w, rid))
        if t is None:
            miss_src += 1
        elif e['first'] < t:
            anomalies['admission_before_source_receipt'] += 1
        else:
            s2a.append(e['first'] - t)
        p = proj_first.get((w, chat, rid))
        if p is None:
            miss_proj += 1
        elif p < e['first']:
            anomalies['projection_before_admission'] += 1
        else:
            a2p.append(p - e['first'])
    stage_series = dict(
        source_receipt_to_admission=dict(stats(s2a, denominator=len(events)), no_source_receipt=miss_src),
        admission_to_projection_start=dict(stats(a2p, denominator=len(events)), no_projection_start=miss_proj))

    # ---- captures / projections: attempts, right-censoring, dispositions ----
    def attempts(start_ev, end_ev, idf):
        starts, ends, orphan = {}, {}, 0
        for r in recs:
            i = r.get(idf)
            if r['event'] not in (start_ev, end_ev):
                continue
            if not _s(i):
                ctx.reject(f'{idf}', r); continue
            (starts if r['event'] == start_ev else ends).setdefault((r['window_id'], i), r)
        orphan = sum(k not in starts for k in ends)
        return starts, ends, orphan

    def io_report(starts, ends):
        """Only explicit available=True counters are evidence; absent costs stay unknown."""
        costs = [e.get('io_cost') for k, e in ends.items() if k in starts]
        report = {}
        for field in ('db_opens', 'sql_calls', 'network_calls', 'rows_returned', 'vm_steps'):
            vals = [c[field] for c in costs if isinstance(c, dict) and c.get('available') is True
                    and clock(c.get(field))]
            complete = len(vals) == len(costs) and bool(costs)
            report[field] = dict(samples=len(vals), unknown=len(costs) - len(vals),
                                 observed_total=sum(vals) if vals else None,
                                 total=sum(vals) if complete else None, complete=complete)
        return report

    def completed(starts, ends, key=None):
        wall, cpu, disp, io = [], [], Counter(), Counter()
        for k, e in ends.items():
            if k not in starts:
                continue
            if clock(e.get('elapsed_ns')):
                wall.append(e['elapsed_ns'])
            if clock(e.get('thread_cpu_ns')):
                cpu.append(e['thread_cpu_ns'])
            disp[e.get('disposition') if _s(e.get('disposition')) else 'unknown'] += 1
            ioc = e.get('io_cost')
            io['available' if isinstance(ioc, dict) and ioc.get('available') is not False and ioc else
               'unavailable' if isinstance(ioc, dict) else 'missing'] += 1
        return wall, cpu, disp, io

    def censored(starts, ends):
        c = [k for k in starts if k not in ends]
        ages = [obs_end(k[0]) - starts[k]['monotonic_ns'] for k in c]
        return dict(count=len(c), age_lower_bound_ns=stats([a for a in ages if a >= 0])['max_ns'],
                    unclosed_windows=sum(not win[k[0]]['closed'] for k in c))

    cs, ce, c_orphan = attempts('capture_start', 'capture_end', 'capture_attempt_id')
    cw, cc, cd, cio = completed(cs, ce)
    capture_report = dict(started=len(cs), ended=sum(k in cs for k in ce), end_without_start=c_orphan,
                          right_censored=censored(cs, ce), dispositions=dict(cd),
                          by_domain=dict(Counter(r.get('domain') if r.get('domain') in ('local', 'external') else 'unknown'
                                                 for r in cs.values())),
                          wall=stats(cw), thread_cpu=stats(cc), io_cost_evidence=dict(cio), io_cost=io_report(cs, ce))
    ps, pe, p_orphan = attempts('projection_start', 'projection_end', 'projection_attempt_id')
    pw, pc, pd, pio = completed(ps, pe)
    projection_report = dict(started=len(ps), ended=sum(k in ps for k in pe), end_without_start=p_orphan,
                             right_censored=censored(ps, pe), dispositions=dict(pd),
                             failures=pd.get('error', 0), superseded=pd.get('superseded', 0),
                             forgotten=pd.get('forgotten', 0), lineage_changed=pd.get('lineage_changed', 0),
                             wall=stats(pw), thread_cpu=stats(pc), io_cost_evidence=dict(pio), io_cost=io_report(ps, pe))

    # ---- writers ----
    wr = {}
    for r in recs:
        if r['event'] in ('writer_request', 'writer_acquire', 'writer_release'):
            if not _s(r.get('writer_id')):
                ctx.reject('writer_id', r); continue
            wr.setdefault((r['window_id'], r['writer_id']), {})[r['event']] = r
    waits, holds, totals = [], [], []
    wc = Counter()
    kinds = Counter()
    for k, d in wr.items():
        rq, ac, rl = d.get('writer_request'), d.get('writer_acquire'), d.get('writer_release')
        kinds[(rq or ac or rl).get('producer_kind') if _s((rq or ac or rl).get('producer_kind')) else 'unknown'] += 1
        if rq is None:
            wc['without_request'] += 1; continue
        if not clock(rq.get('requested_ns')):
            wc['bad_clock'] += 1; continue
        if rl is None:
            if ac is None:
                wc['right_censored_waiting'] += 1
            else:
                wc['right_censored_holding'] += 1
            if ac is not None and not clock(ac.get('acquired_ns')):
                wc['bad_clock'] += 1
            continue
        if rl.get('acquired') is not True:
            wc['refused_or_timed_out'] += 1; continue
        a_ns, r_ns = rl.get('acquired_ns') if clock(rl.get('acquired_ns')) else (ac or {}).get('acquired_ns'), rl.get('released_ns')
        if not (clock(a_ns) and clock(r_ns)) or not rq['requested_ns'] <= a_ns <= r_ns:
            wc['bad_clock'] += 1; continue
        waits.append(a_ns - rq['requested_ns']); holds.append(r_ns - a_ns); totals.append(r_ns - rq['requested_ns'])
        wc['completed'] += 1
    writer_report = dict(writers=len(wr), counts=dict(wc), producer_kinds=dict(kinds), wait=stats(waits),
                         hold=stats(holds), request_to_release=stats(totals),
                         diagnostics_complete=bool(wr) and wc['without_request'] == 0 and wc['bad_clock'] == 0)

    # ---- separate outcome counts (never lumped into successes) ----
    ev = Counter(r['event'] for r in recs)
    invalidation_only = sum(r['event'] == 'emit_success' and r.get('kind') == 'invalidation_only' for r in recs)
    outcomes = dict(no_change=ev['no_change'], superseded=ev['superseded'], forgotten=ev['forgotten'],
                    source_error=ev['source_error'], emit_error=ev['emit_error'],
                    catchup_error=ev['catchup_error'], emit_invalidation_only=invalidation_only,
                    model_stored=ev['model_stored'], admitted_distinct=len(adm),
                    admitted_duplicate_observations=sum(v - 1 for v in adm_obs.values()),
                    rejected_inclusion_records=rejected_inclusions)

    inputs_valid = bool(validator['valid'] and not integrity and dup_identical == 0 and dup_conflict == 0
                        and not ctx.rejected and not lossy)
    return dict(
        schema='panel-source-analysis/1',
        acceptance=NOT_EVALUATED, semantic_oracle=NOT_EVALUATED, native_desktop_p95=NOT_EVALUATED,
        cpu_gate=NOT_EVALUATED,
        note='timings alone never produce latency or C acceptance PASS; expected row/action/absence oracle absent',
        inputs_valid=inputs_valid,
        validator={k: validator[k] for k in ('valid', 'errors', 'stored', 'fulfilled', 'server_emitted',
                                              'authenticated_catchup')} | {'unmatched_publications': validator['unmatched']},
        integrity_errors=integrity, anomalies=dict(anomalies),
        records=dict(input=len(records), analyzed=len(recs), rejected=ctx.rejected,
                     reject_reasons=dict(ctx.reject_reasons), reject_samples=ctx.samples,
                     duplicate_identical=dup_identical, duplicate_conflicting=dup_conflict),
        log_gaps=log_gaps,
        episodes=episode_report, events=event_report, failures_over_budget=failures_over_budget,
        stages=stage_series, captures=capture_report, projections=projection_report, writers=writer_report,
        outcomes=outcomes)


def _canon_safe(v):
    try:
        return _canon(v)
    except (TypeError, ValueError):
        return repr(v)


def main(argv=None):
    """Offline analysis of caller-provided JSONL segments; prints JSON, never activates."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('segments', nargs='+', type=Path)
    parser.add_argument('--allow-open', action='store_true')
    args = parser.parse_args(argv)
    records = []
    try:
        size = 0
        for p in args.segments:
            with p.open('rb') as s:
                while True:
                    line = s.readline(MAX_LINE_BYTES + 1)
                    if not line:
                        break
                    size += len(line)
                    if len(line) > MAX_LINE_BYTES or size > MAX_INPUT_BYTES or len(records) >= MAX_RECORDS:
                        raise AnalysisBounds('file input bound exceeded')
                    if line.strip():
                        records.append(json.loads(line))
        out = analyze_records(records, require_closed=not args.allow_open)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        out = {'acceptance': NOT_EVALUATED, 'inputs_valid': False, 'error': type(exc).__name__}
    print(json.dumps(out, sort_keys=True))
    return 0 if out.get('inputs_valid') else 1


if __name__ == '__main__':
    raise SystemExit(main())
