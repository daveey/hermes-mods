"""Cached reads and bounded persistent refresh requests; never remote I/O."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import itertools
import json
import marshal
import math
import os
import pickle
from datetime import datetime
from pathlib import Path
import sqlite3
import threading
import time

from .collector import ident, iso, readiness, operational_evidence, readiness_evidence
from .health import State, project, Policy
from .models import validate


# Served by store.py's runner_entity expression index (schema v2): the
# json_extract expression must stay textually equivalent to the indexed one.
OBSERVATION_HISTORY_SQL = ("SELECT payload FROM runner_records WHERE kind='Observation' AND "
                           "json_extract(payload,'$.entity_id')=? ORDER BY updated_at DESC LIMIT ?")
INCIDENT_HISTORY_SQL = ("SELECT payload FROM runner_records WHERE kind='Incident' AND "
                        "json_extract(payload,'$.entity_id')=? ORDER BY updated_at DESC LIMIT ?")


def connect(path, *, write=False):
    uri = Path(path).resolve().as_uri()
    if write:
        return sqlite3.connect(uri+'?mode=rw', uri=True, timeout=1)
    try:
        db=sqlite3.connect(uri+'?mode=ro',uri=True,timeout=1)
        db.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchone()
    except sqlite3.OperationalError:
        try:
            db.close()
        except UnboundLocalError:
            pass
        db=sqlite3.connect(uri+'?mode=rw',uri=True,timeout=1)
    db.execute('PRAGMA query_only=ON')
    return db


# Change-keyed reads (design t_01a1d563324d7e8e card L). Each dashboard and tool read decoded the 2.4 MB snapshot
# row and deep-copied every runner row: ~56 ms of CPU per inventory() on the hub (1.9k runners), and the fleet
# panel decoded the snapshot a second time for host load. Now:
#   * ``_read`` memoizes the decoded snapshot and the maintenance overlay on the state file's and its WAL's
#     (ino, size, mtime_ns); a moved key re-reads the row and decodes it only when its text changed;
#   * ``inventory`` memoizes its result on (that snapshot, the overlay, the config's fingerprint) for the clock
#     window in which none of its time comparisons can change (``_flips``), and hands out copies (or, with
#     shared=True, the memo itself to read-only callers);
#   * the P1 fallback ceiling: neither memo is used for longer than REUSE_MAX_S, so a write the file key missed or a
#     time comparison ``_flips`` does not know of is seen that late at most.
REUSE_MAX_S = 30.0
TOOLS_TTL_S = 900  # collector.readiness: tools_observed_at counts for 900 s
FLIP_EARLY_S = 1e-3
_MEMO_LOCK = threading.Lock()
_GENERATION = itertools.count(1)
_READS: dict = {}  # state file path -> _Read
_RESULTS: dict = {}  # state file path -> _Result
_UNREAD = object()
_NO_CARRY = object()
ROWS_MAX_S = 300.0  # a carried runner row is computed afresh at least this often (its P1 ceiling)


class _Carry:
    """One build's per-row results, reusable by the next build: rows maps a runner id to (its snapshot row, its
    state, the output row, its projection, its provenance entry, when computed); hosts maps a host id to (its
    host result, its maintenance) as that build saw them."""

    __slots__ = ('current', 'built_at', 'rows', 'hosts', 'computed')

    def __init__(self, current, built_at):
        self.current, self.built_at = current, built_at
        self.rows, self.hosts, self.computed = {}, {}, 0


@dataclass
class _Read:
    key: tuple
    read_at: float
    text: object
    snapshot: object
    generation: int
    overlay: object


class _Result:
    """A memoized inventory. ``shared`` is never handed to a caller that may mutate it: copies are unpickled
    from ``blob``, pickled on the first such request (a fleet panel that only reads never pays for it)."""

    __slots__ = ('key', 'built_at', 'until', 'shared', 'blob', 'lock', 'carry')

    def __init__(self, key, built_at, until, shared, carry=None):
        self.key, self.built_at, self.until, self.shared = key, built_at, until, shared
        self.carry = carry
        self.blob = None
        self.lock = threading.Lock()

    def view(self, shared, now):
        fresh = dict(request_id=ident('request'), generated_at=iso(now))
        if shared:
            return dict(self.shared, meta=dict(self.shared['meta'], **fresh))
        with self.lock:
            if self.blob is None:
                self.blob = pickle.dumps(self.shared, protocol=pickle.HIGHEST_PROTOCOL)
        result = pickle.loads(self.blob)
        result['meta'].update(fresh)
        return result


def _next_flip(stamps, ttls, now):
    """min(x + d >= now) over numeric stamps x and thresholds d: a `0 <= now - x <= d` test can change its answer
    only when the clock crosses x or x + d (at x + d itself it still holds: it flips just after, and >= keeps
    the bound at or before that). Each bound is FLIP_EARLY_S early: provenance compares the clock with x + d
    after an ISO round trip, which rounds to the microsecond."""
    best = math.inf
    for x in stamps:
        if type(x) not in (int, float):
            continue
        try:
            if not math.isfinite(x):
                continue
        except OverflowError:  # an int beyond float range: its thresholds are never reached
            continue
        for d in ttls:
            # Bounds in (now - FLIP_EARLY_S, now] stay: they end the memo at once, until the clock passes x + d.
            if now <= x + d and x + d - FLIP_EARLY_S < best:
                best = x + d - FLIP_EARLY_S
    return best


_LIVENESS_ONLY = dict(processed_at=None, cycle=None, absent_cycles=None, healthy_cycles=None)


def _same_state(a, b):
    """Runner states equal but for the collector's per-cycle bookkeeping: ``health.advance`` reads these, the
    projection never does. Of them the inventory shows only ``cycle`` (as provenance ``collection_id``), which
    ``_inventory`` patches on a carried row."""
    if a == b:
        return True
    return (type(a) is dict and type(b) is dict
            and dict(a, **_LIVENESS_ONLY) == dict(b, **_LIVENESS_ONLY))


def _file_key(path):
    out = []
    for name in (path, path + '-wal'):
        try:
            st = os.stat(name)
        except FileNotFoundError:
            out.append(None)
        else:
            out.append((st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns))
    return tuple(out)


def _fingerprint(config):
    """Digest of the whole config (a Service gets a fresh copy per call), or None when it holds a type marshal
    cannot encode: then nothing is memoized."""
    try:
        return hashlib.blake2b(marshal.dumps(config), digest_size=16).digest()
    except ValueError:
        return None


class _NoAccounting:
    """Stand-in for core's ``loop_accounting.Pass`` outside a Hermes runtime (or on an older core)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def skip(self):
        pass

    def change(self, changed=True):
        pass

    def fallback(self, fell_back=True):
        pass

    def rows(self, n):
        pass


def _accounted_pass(name):
    """One P1 loop-accounting pass (core ``hermes_cli.loop_accounting``), or a no-op without it."""
    try:
        from hermes_cli.loop_accounting import pass_
    except Exception:  # outside a Hermes runtime, or a core without P1
        return _NoAccounting()
    return pass_(name)


class Service:
    def __init__(self,path,config,*,clock=time.time,fingerprint=None):
        self.path,self.config,self.clock=path,config,clock
        # The caller's precomputed config_fingerprint(config) (the dashboard caches it with the parsed config).
        self._fingerprint=fingerprint

    @staticmethod
    def config_fingerprint(config):
        return _fingerprint(config)

    def _snapshot(self):
        """The collector snapshot. It is shared between calls (see ``_read``): never mutate it."""
        return self._owned(self._read().snapshot)

    def _owned(self, snapshot):
        owner={k:self.config[k] for k in ('owner_gateway_id','owner_profile','owner_epoch','config_revision')}
        if snapshot and snapshot.get('owner') != owner:
            raise RuntimeError('owner_conflict')
        return snapshot

    def _read(self):
        """The decoded snapshot and maintenance overlay, re-read only when the state file's key moved."""
        path = str(self.path)
        key = _file_key(path)  # before the read: a write racing it moves the key again
        now = time.monotonic()
        with _MEMO_LOCK:
            memo = _READS.get(path)
        if memo is not None and memo.key == key and 0 <= now - memo.read_at < REUSE_MAX_S:
            return memo
        db=connect(self.path)
        try:
            row=db.execute("SELECT payload FROM collector_cache WHERE key='snapshot'").fetchone()
            overlay=None
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='repair_domain_maintenance'").fetchone():
                overlay=tuple(db.execute('SELECT entity_id,payload FROM repair_domain_maintenance ORDER BY entity_id'))
        finally:
            db.close()
        text = row[0] if row else None
        if memo is not None and memo.text == text:
            snapshot, generation = memo.snapshot, memo.generation  # rewritten, not changed: no decode
        else:
            snapshot, generation = (json.loads(text) if text else None), next(_GENERATION)
        fresh = _Read(key, now, text, snapshot, generation, overlay)
        with _MEMO_LOCK:
            _READS[path] = fresh
        return fresh

    def _meta(self,snapshot,*,shared=False):
        # shared=True (a memoized build): coverage and source errors stay the shared snapshot's own (never mutated
        # here or by a reader), provenance entries are copied one level (their state is set below).
        copy = (lambda v: v) if shared else deepcopy
        prov = (lambda v: [dict(p) for p in v]) if shared else deepcopy
        now=self.clock(); c=self.config; at=snapshot['at'] if snapshot else None
        ttl=c['thresholds']['inventory_stale_seconds']
        return dict(contract_version=1,request_id=ident('request'),owner=dict(gateway_id=c['owner_gateway_id'],profile=c['owner_profile'],owner_epoch=c['owner_epoch'],config_revision=c['config_revision']),
                    generated_at=iso(now),observed_at=iso(at) if at is not None else None,expires_at=iso(at+ttl) if at is not None else None,
                    freshness=('fresh' if 0<=now-at<=ttl else 'stale') if at is not None else 'unknown',
                    provenance=prov(snapshot['provenance']) if snapshot else [],coverage=copy(snapshot['coverage']) if snapshot else [],source_errors=copy(snapshot['source_errors']) if snapshot else [])

    def inventory(self, *, shared=False):
        """The inventory. ``shared=True`` returns the memoized result itself: the caller must not mutate it."""
        if not self.config['features']['observe']:
            raise RuntimeError('feature_disabled')
        with _accounted_pass('runners.inventory') as acct:
            read = self._read()
            snap = self._owned(read.snapshot)
            fingerprint = self._fingerprint if self._fingerprint is not None else _fingerprint(self.config)
            path, now = str(self.path), self.clock()
            key = (read.generation, read.overlay, fingerprint)
            with _MEMO_LOCK:
                memo = _RESULTS.get(path)
            if (fingerprint is not None and memo is not None and memo.key == key
                    and memo.built_at <= now < memo.until):
                acct.skip()
                return memo.view(shared, now)
            acct.change(memo is None or memo.key != key)
            acct.fallback(memo is not None and memo.key == key)  # a flip instant or the REUSE_MAX_S ceiling
            if fingerprint is None:
                return self._inventory(snap, read.overlay)  # no memo: the caller owns a deep result
            # Rows of an earlier build carry over while their inputs are equal (but for liveness-only fields) and
            # none of their own time comparisons has flipped since they were computed (see _inventory).
            carry = memo.carry if memo is not None and memo.key[2] == fingerprint else None
            # Rows share nested values with the shared snapshot here: the memo hands out only pickled copies
            # or, to read-only callers, the shared result (see _Result.view).
            result, rows = self._inventory(snap, read.overlay, copy_rows=False, carry=carry, now=now)
            acct.rows(rows.computed)
            until = min(self._flips(snap, result['data']['hosts'], now), now + REUSE_MAX_S)
            fresh = _Result(key, now, until, result, rows)
            with _MEMO_LOCK:
                _RESULTS[path] = fresh
            return fresh.view(shared, now)

    def _ttl(self, name):
        v = self.config['thresholds'].get(name)
        return v if type(v) in (int, float) and math.isfinite(v) else 0

    def _host_flip(self, host_result, maintenance, now):
        """The earliest instant at or after ``now`` at which a comparison of the clock with this host's evidence
        or maintenance can change its answer (the host result's, its facts' and its evidence's timestamps against
        0, the host threshold and the tools' 900 s; the maintenance expiry)."""
        stamps = []
        if host_result:
            stamps.append(host_result.get('at'))
            facts = host_result.get('facts') or {}
            stamps.extend(v for k, v in facts.items() if k.endswith('_at'))
            for items in (facts.get('operational_evidence') or {}).values():
                for item in items:
                    stamps.extend((item.get('observed_at'), item.get('recorded_at')))
        flip = _next_flip(stamps, (0, self._ttl('host_stale_seconds'), TOOLS_TTL_S), now)
        expires = (maintenance or {}).get('expires_at') if (maintenance or {}).get('state') == 'active' else None
        if expires:
            at = datetime.fromisoformat(expires.replace('Z', '+00:00')).timestamp()
            if at >= now:
                flip = min(flip, at)
        return flip

    def _flips(self, snap, hosts, now):
        """The earliest instant at or after ``now`` at which a time comparison in ``_inventory`` can change its
        answer: the snapshot's and each runner state's timestamp against 0 and the inventory threshold, the expiry of
        each fresh provenance entry of the snapshot's own, and each
        host's evidence and maintenance (``_host_flip``)."""
        ttls = (0, self._ttl('inventory_stale_seconds'))
        flips = [_next_flip([snap.get('at')] if snap else [], ttls, now)]
        if snap:
            flips.append(_next_flip([state.get('at') for state in snap.get('states', {}).values()], ttls, now))
            # The collector's own provenance entries: a fresh one turns stale once the clock passes its expiry.
            expiries = []
            for item in snap.get('provenance') or ():
                if type(item) is dict and item.get('state') == 'fresh' and type(item.get('expires_at')) is str:
                    try:
                        expiries.append(datetime.fromisoformat(item['expires_at'].replace('Z', '+00:00')).timestamp())
                    except (ValueError, OverflowError, OSError):
                        pass
            flips.append(_next_flip(expiries, (0,), now))
            # Every host result, mapped to a configured host or not (a row may name any host id).
            maintenance = {host['id']: host.get('maintenance') for host in hosts}
            for hid, result in snap.get('host_results', {}).items():
                flips.append(self._host_flip(result, maintenance.get(hid), now))
        flips.extend(self._host_flip(None, host.get('maintenance'), now) for host in hosts)
        return min(flips)

    def _inventory(self, snap, overlay=_UNREAD, *, copy_rows=True, carry=_NO_CARRY, now=None):
        """The inventory. With ``carry`` (an earlier build's ``_Carry`` or None) it also returns this build's
        ``_Carry``, and reuses each runner row whose inputs equal the carried ones (see ``inventory``)."""
        tracked = carry is not _NO_CARRY
        meta=self._meta(snap, shared=not copy_rows); c=self.config; rows=[]; projections=[]
        hosts=deepcopy(c['hosts'])
        if overlay is _UNREAD:
            overlay = self._read().overlay
        if overlay is not None:
            maintenance={entity_id:json.loads(payload) for entity_id,payload in overlay}
            for host in hosts:
                host['maintenance']=maintenance.get(host['resource_id'],host['maintenance'])
        for host in hosts:
            m=host['maintenance']
            if m['state']=='active' and datetime.fromisoformat(m['expires_at'].replace('Z','+00:00')).timestamp()<=self.clock():
                host['maintenance']=dict(state='inactive',reason=None,expires_at=None)
        policy=Policy(inventory_ttl=c['thresholds']['inventory_stale_seconds'],host_ttl=c['thresholds']['host_stale_seconds'])
        current = meta['freshness'] == 'fresh'
        host_results = snap.get('host_results', {}) if snap else {}
        built = _Carry(current, now) if tracked else None
        if carry is _NO_CARRY or (carry is not None and carry.current != current):
            carry = None
        same_host = {}  # host id -> its result and maintenance equal the carried ones
        host_flip = {}  # host id -> _host_flip
        for src in snap['runners'] if snap else ():
            rid, hid = src['id'], src.get('host_id')
            observed = snap['states'][rid]
            maintenance=next((h['maintenance'] for h in hosts if h['id']==hid), {})
            if tracked:
                inputs = (host_results.get(hid), maintenance)
                built.hosts[hid] = inputs
                entry = carry.rows.get(rid) if carry is not None else None
                if entry is not None:
                    if hid not in same_host:
                        same_host[hid] = carry.hosts.get(hid, _NO_CARRY) == inputs
                    if (same_host[hid] and entry[5] <= now < min(entry[6], entry[5] + ROWS_MAX_S)
                            and entry[0] == src and _same_state(entry[1], observed)):
                        provenance = entry[4]
                        if provenance is not None and provenance['collection_id'] != observed['cycle']:
                            provenance = dict(provenance, collection_id=observed['cycle'])
                            entry = entry[:4] + (provenance,) + entry[5:]
                        built.rows[rid] = entry
                        rows.append(entry[2]); projections.append(entry[3])
                        if provenance is not None:
                            meta['provenance'].append(dict(provenance))
                        continue
                built.computed += 1
            # Only top-level keys of a row are set below: a memoized build (copy_rows=False) may share the rest.
            row=deepcopy(src) if copy_rows else dict(src)
            checks, ready=readiness(c,row,host_results,self.clock(), snapshot_current=current)
            # This is host projection data, never provider-supplied runner data.
            row.pop('operational_evidence', None)
            row.pop('readiness_evidence', None)
            causes = readiness_evidence(c, row, host_results, self.clock(), checks, snapshot_current=current)
            if causes is not None:
                row['readiness_evidence'] = causes
            operational = operational_evidence(c, row, host_results, self.clock(), snapshot_current=current)
            if operational:
                row['operational_evidence'] = operational
            host=host_results.get(hid,{})
            until=(datetime.fromisoformat(maintenance['expires_at'].replace('Z','+00:00')).timestamp()
                   if maintenance.get('state')=='active' else None)
            p=project(State(**observed),now=self.clock(),policy=policy,checks=checks,management=host.get('management','unknown'),host_at=host.get('at'),maintenance_until=until)
            row.update(registration=p.registration,activity=p.activity,freshness=p.freshness,readiness=ready)
            rows.append(row); projections.append(p)
            at = observed.get('at')
            provenance = None
            if at is not None:
                provenance = dict(
                    source_id=row['id'], collection_id=observed['cycle'],
                    observed_at=iso(at), expires_at=iso(at+policy.inventory_ttl),
                    state='fresh' if p.freshness == 'fresh' and not observed.get('source_error') else 'stale',
                    observation_ids=row['observation_ids'])
                meta['provenance'].append(dict(provenance) if tracked else provenance)
            if tracked:
                if hid not in host_flip:
                    host_flip[hid] = self._host_flip(host_results.get(hid), maintenance, now)
                flip = min(host_flip[hid], _next_flip((observed.get('at'),), (0, policy.inventory_ttl), now))
                built.rows[rid] = (src, observed, row, p, provenance, now, flip)
        for provenance in meta['provenance']:
            if provenance['state']=='fresh' and (meta['freshness']!='fresh' or self.clock()>datetime.fromisoformat(provenance['expires_at'].replace('Z','+00:00')).timestamp()): provenance['state']='stale'
        # online/busy/unverified describe CURRENT evidence (plan §3/§5: stale inventory keeps
        # last-known values per row but never counts as live); same rule as the Desktop chip.
        current=[p for p,row in zip(projections,rows) if p.freshness=='fresh' and not snap['states'][row['id']].get('source_error') and p.registration=='online']
        count=dict(registrations=len(rows),online=len(current),busy=sum(p.activity=='busy' for p in current),verified_ready=sum(r.ready for r in projections),readiness_unverified=sum(not p.ready for p in current),issues=sum('source_unavailable' in r.reasons or r.health in ('missing','offline','unavailable','degraded','stale','unknown','suspected_offline') for r in projections),mapped_hosts=len({r['host_id'] for r in rows if r['host_id']}),host_capacity=None)
        state='loading' if not snap else ('ready' if snap['collection']['state']=='complete' else 'partial')
        if snap and any(p['state']!='fresh' for p in meta['provenance']): state='partial'
        if not c['scopes']: state='not_configured'
        if snap and meta['freshness']=='stale': state='owner_disconnected'
        for host in hosts:
            evidence=(snap or {}).get('host_results',{}).get(host['id'])
            at = evidence['at'] if evidence else None
            fresh = at is not None and 0 <= self.clock()-at <= policy.host_ttl
            if fresh:
                host['management']=evidence['management']
            host['observation_ids'] = list(evidence.get('observation_ids', [])) if evidence else []
            meta['provenance'].append(dict(
                source_id=host['resource_id'], collection_id=None,
                observed_at=iso(at) if at is not None else None,
                expires_at=iso(at+policy.host_ttl) if at is not None else None,
                state=('fresh' if evidence['management']=='reachable' else 'unavailable') if fresh else ('stale' if evidence else 'unknown'),
                observation_ids=host['observation_ids']))
        mapped_hosts={row['host_id'] for row in rows if row['host_id']}
        count['issues'] += sum(host['id'] in mapped_hosts and host['management']=='unreachable' for host in hosts)
        data=dict(state=state,collection=snap['collection'] if snap else None,counts=count,hosts=hosts,runners=rows,scopes=c['scopes'],providers=c['providers'],targets=c['execution_targets'])
        if tracked:
            return dict(meta=meta,data=data), built
        return dict(meta=meta,data=data)

    def inspect(self,runner_id,history_limit=100,*,principal=None):
        validate('InspectRequest',dict(runner_id=runner_id,history_limit=history_limit))
        if not self.config['features']['observe']:
            raise RuntimeError('feature_disabled')
        snap=self._snapshot()
        inventory=self._inventory(snap)
        runner=next((r for r in inventory['data']['runners'] if r['id']==runner_id),None)
        if runner is None: raise KeyError('not_found')
        db=connect(self.path)
        try:
            # JSON filtering occurs before LIMIT; do not silently lose older matching rows.
            observations=[json.loads(r[0]) for r in db.execute(OBSERVATION_HISTORY_SQL,(runner_id,history_limit))]
            incidents=[json.loads(r[0]) for r in db.execute(INCIDENT_HISTORY_SQL,(runner_id,100))]
            repairs=[]
            if principal and self.config['features']['repair_sessions'] and db.execute("SELECT 1 FROM sqlite_master WHERE name='repair_domain_records'").fetchone():
                scope='host:'+runner['host_id'] if runner.get('host_id') else 'runner:'+runner_id
                owner=json.dumps(inventory['meta']['owner'],sort_keys=True,separators=(',',':'),ensure_ascii=True)
                repairs=[json.loads(r[0]) for r in db.execute('SELECT payload FROM repair_domain_records WHERE scope=? AND principal=? AND owner=? ORDER BY rowid DESC LIMIT 100',(scope,principal,owner))]
        finally: db.close()
        monitoring, jobs = self._job_evidence(snap, runner, history_limit)
        return dict(meta=inventory['meta'],data=dict(runner=runner,observations=observations,incidents=incidents,repairs=repairs,jobs=[],job_monitoring=monitoring,observed_jobs=jobs))

    def _job_evidence(self, snap, runner, limit):
        """External observations are not dispatched Job records or health proof."""
        sid=runner['scope_id']; evidence=snap.get('job_results', {}).get(sid)
        configured=snap.get('job_monitoring_scopes')
        at=evidence.get('observed_at') if evidence else None
        ttl=self.config['thresholds']['inventory_stale_seconds']
        fresh=at is not None and 0<=self.clock()-at<=ttl
        state,reason='unknown','not_observed'
        if configured is not None and sid not in configured:
            state,reason='not_configured','not_configured'
            evidence=None; at=None
        elif evidence and configured is not None:
            error=evidence.get('error')
            if not fresh: state,reason='unknown','stale_evidence'
            elif error=='forbidden': state,reason='forbidden',error
            elif evidence['complete'] or error=='enrichment_truncated':
                state,reason='configured',error or 'bounded_query_complete'
            else: state,reason='unavailable',error or 'incomplete'
        monitoring=dict(state=state,reason_code=reason,observed_at=iso(at) if at is not None else None,
                        expires_at=iso(at+ttl) if at is not None else None)
        rows=[]
        for row in (evidence or {}).get('rows', []):
            if row['runner_id'] != runner['provider_runner_id']:
                continue
            rows.append(dict(row, observed_at=iso(at), expires_at=iso(at+ttl),
                url=f"https://github.com/{row['repository']}/actions/runs/{row['run_id']}/job/{row['job_id']}"))
            if len(rows)>=limit: break
        return monitoring, rows if limit else []

    def refresh(self,request):
        request=validate('RefreshRequest',request)
        inv=self.inventory(); c=self.config
        allowed={s['id'] for s in c['scopes']}|{h['resource_id'] for h in c['hosts']}|{r['id'] for r in inv['data']['runners']}
        if not set(request['target_ids'])<=allowed: raise ValueError('unsafe_target')
        targets=json.dumps(sorted(request['target_ids'])); now=self.clock()
        db=connect(self.path,write=True)
        try:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT targets,collection_id FROM collector_request_keys WHERE key=?',(request['idempotency_key'],)).fetchone()
            if old and old[0]!=targets: raise ValueError('idempotency_conflict')
            row=db.execute('SELECT id,state FROM collector_refresh WHERE id=?',(old[1],)).fetchone() if old else db.execute("SELECT id,state FROM collector_refresh WHERE targets=? AND state IN ('pending','running') ORDER BY created LIMIT 1",(targets,)).fetchone()
            if row is None:
                if db.execute("SELECT count(*) FROM collector_refresh WHERE state IN ('pending','running')").fetchone()[0]>=100: raise RuntimeError('rate_limited')
                row=(ident('collection'),'pending')
                db.execute('INSERT INTO collector_refresh VALUES (?,?,?,?)',(row[0],targets,row[1],now))
            if not old:
                if db.execute('SELECT count(*) FROM collector_request_keys').fetchone()[0]>=10000: raise RuntimeError('rate_limited')
                db.execute('INSERT INTO collector_request_keys VALUES (?,?,?,?)',(request['idempotency_key'],targets,row[0],now))
            db.commit()
            return dict(meta=inv['meta'],data=dict(id=row[0],state=row[1],target_ids=request['target_ids']))
        finally: db.close()
