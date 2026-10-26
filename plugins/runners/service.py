"""Cached reads and bounded persistent refresh requests; never remote I/O."""
from copy import deepcopy
import json
from datetime import datetime
from pathlib import Path
import sqlite3
import time

from .collector import ident, iso, readiness
from .health import State, project, Policy
from .models import validate


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


class Service:
    def __init__(self,path,config,*,clock=time.time):
        self.path,self.config,self.clock=path,config,clock

    def _snapshot(self):
        db=connect(self.path)
        try:
            row=db.execute("SELECT payload FROM collector_cache WHERE key='snapshot'").fetchone()
            snapshot=json.loads(row[0]) if row else None
            owner={k:self.config[k] for k in ('owner_gateway_id','owner_profile','owner_epoch','config_revision')}
            if snapshot and snapshot.get('owner') != owner:
                raise RuntimeError('owner_conflict')
            return snapshot
        finally:
            db.close()

    def _meta(self,snapshot):
        now=self.clock(); c=self.config; at=snapshot['at'] if snapshot else None
        ttl=c['thresholds']['inventory_stale_seconds']
        return dict(contract_version=1,request_id=ident('request'),owner=dict(gateway_id=c['owner_gateway_id'],profile=c['owner_profile'],owner_epoch=c['owner_epoch'],config_revision=c['config_revision']),
                    generated_at=iso(now),observed_at=iso(at) if at is not None else None,expires_at=iso(at+ttl) if at is not None else None,
                    freshness=('fresh' if 0<=now-at<=ttl else 'stale') if at is not None else 'unknown',
                    provenance=deepcopy(snapshot['provenance']) if snapshot else [],coverage=deepcopy(snapshot['coverage']) if snapshot else [],source_errors=deepcopy(snapshot['source_errors']) if snapshot else [])

    def inventory(self):
        if not self.config['features']['observe']:
            raise RuntimeError('feature_disabled')
        snap=self._snapshot(); meta=self._meta(snap); c=self.config; rows=[]; projections=[]
        policy=Policy(inventory_ttl=c['thresholds']['inventory_stale_seconds'],host_ttl=c['thresholds']['host_stale_seconds'])
        for row in snap['runners'] if snap else ():
            row=deepcopy(row)
            checks, ready=readiness(c,row,snap.get('host_results',{}),self.clock())
            host=snap.get('host_results',{}).get(row.get('host_id'),{})
            p=project(State(**snap['states'][row['id']]),now=self.clock(),policy=policy,checks=checks,management=host.get('management','unknown'),host_at=host.get('at'))
            row.update(registration=p.registration,activity=p.activity,freshness=p.freshness,readiness=ready)
            rows.append(row); projections.append(p)
            observed = snap['states'][row['id']]
            at = observed.get('at')
            if at is not None:
                meta['provenance'].append(dict(
                    source_id=row['id'], collection_id=observed['cycle'],
                    observed_at=iso(at), expires_at=iso(at+policy.inventory_ttl),
                    state='fresh' if p.freshness == 'fresh' and not observed.get('source_error') else 'stale',
                    observation_ids=row['observation_ids']))
        for provenance in meta['provenance']:
            if provenance['state']=='fresh' and (meta['freshness']!='fresh' or self.clock()>datetime.fromisoformat(provenance['expires_at'].replace('Z','+00:00')).timestamp()): provenance['state']='stale'
        count=dict(registrations=len(rows),online=sum(r.registration=='online' for r in projections),busy=sum(r.activity=='busy' for r in projections),verified_ready=sum(r.ready for r in projections),readiness_unverified=sum(r.registration=='online' and not r.ready for r in projections),issues=sum(r.health in ('missing','offline','unavailable','degraded','stale','unknown','suspected_offline') for r in projections),mapped_hosts=len({r['host_id'] for r in rows if r['host_id']}),host_capacity=None)
        state='loading' if not snap else ('ready' if snap['collection']['state']=='complete' else 'partial')
        if snap and any(p['state']!='fresh' for p in meta['provenance']): state='partial'
        if not c['scopes']: state='not_configured'
        if snap and meta['freshness']=='stale': state='owner_disconnected'
        hosts=deepcopy(c['hosts'])
        for host in hosts:
            evidence=(snap or {}).get('host_results',{}).get(host['id'])
            if evidence and 0<=self.clock()-evidence['at']<=policy.host_ttl:
                host['management']=evidence['management']
        data=dict(state=state,collection=snap['collection'] if snap else None,counts=count,hosts=hosts,runners=rows,scopes=c['scopes'],providers=c['providers'],targets=c['execution_targets'])
        return dict(meta=meta,data=data)

    def inspect(self,runner_id,history_limit=100):
        validate('InspectRequest',dict(runner_id=runner_id,history_limit=history_limit))
        inventory=self.inventory()
        runner=next((r for r in inventory['data']['runners'] if r['id']==runner_id),None)
        if runner is None: raise KeyError('not_found')
        db=connect(self.path)
        try:
            # JSON filtering occurs before LIMIT; do not silently lose older matching rows.
            observations=[json.loads(r[0]) for r in db.execute("SELECT payload FROM runner_records WHERE kind='Observation' AND json_extract(payload,'$.entity_id')=? ORDER BY updated_at DESC LIMIT ?",(runner_id,history_limit))]
        finally: db.close()
        return dict(meta=inventory['meta'],data=dict(runner=runner,observations=observations,incidents=[],repairs=[],jobs=[]))

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
