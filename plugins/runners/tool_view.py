"""Bounded, entity-scoped projections of runners_* results for tool callers.

The REST API and Desktop keep the full envelope (every scope's provenance and
the whole observation history, hundreds of KB on a real fleet). A tool result
larger than the model's inline window is spilled to a file the pinned repair
chat cannot read, so tool handlers return these projections instead: owner and
freshness meta, the subject's own rows, and a hard serialized cap.
"""
import json

CAP_BYTES = 32 * 1024
_META_KEYS = ('contract_version', 'request_id', 'owner', 'generated_at', 'observed_at', 'expires_at', 'freshness')


def size(value):
    return len(json.dumps(value, separators=(',', ':')).encode())


def subject_sources(config, runners, ids):
    """Source ids tied to the subject(s): runner, its scope, its host resource and host id."""
    ids = set(ids)
    hosts = {h['id']: h for h in config.get('hosts', [])}
    out = set(ids)
    for host in config.get('hosts', []):
        if host['resource_id'] in ids or host['id'] in ids:
            out |= {host['id'], host['resource_id']}
    for r in runners:
        host = hosts.get(r.get('host_id'))
        if r['id'] in ids or (host and host['resource_id'] in ids):
            out |= {r['id'], r.get('scope_id')}
            if host:
                out |= {host['id'], host['resource_id']}
    out.discard(None)
    return out


def _mentions(item, sources):
    return isinstance(item, dict) and any(isinstance(v, str) and v in sources for v in item.values())


def scoped_meta(meta, sources):
    out = {k: meta[k] for k in _META_KEYS if k in meta}
    out['provenance'] = [p for p in meta.get('provenance', []) if p.get('source_id') in sources]
    out['coverage'] = [c for c in meta.get('coverage', []) if _mentions(c, sources)]
    out['source_errors'] = [e for e in meta.get('source_errors', []) if _mentions(e, sources)]
    out['scoped_to'] = sorted(sources)
    return out


def _fit(result, trim):
    """Apply trim() (drop the oldest item; return False when nothing is left) until under the cap."""
    result['truncated'] = False
    while size(result) > CAP_BYTES:
        if not trim(result):
            break
        result['truncated'] = True
    if size(result) > CAP_BYTES:  # last resort: never exceed the cap
        result['data'] = dict(error='result_too_large')
        result['truncated'] = True
    return result


def project_inspect(result, config, inventory_runners):
    data = result['data']; runner = data['runner']
    sources = subject_sources(config, inventory_runners, [runner['id']])
    host = next((h for h in config.get('hosts', []) if h['id'] == runner.get('host_id')), None)
    binding = next((b for b in config.get('bindings', []) if b['runner_id'] == runner['id']), None)
    scope = next((s for s in config.get('scopes', []) if s['id'] == runner.get('scope_id')), None)
    out = dict(meta=scoped_meta(result['meta'], sources), data=dict(
        runner=runner, host=host, scope=scope,
        service_refs=[binding['service_ref']] if binding else [],
        observations=list(data.get('observations', [])), incidents=list(data.get('incidents', [])),
        repairs=list(data.get('repairs', [])), job_monitoring=data.get('job_monitoring'),
        observed_jobs=list(data.get('observed_jobs', []))))

    def trim(r):
        d = r['data']
        for key in ('observations', 'observed_jobs', 'incidents', 'repairs'):  # oldest = last (newest-first order)
            if d[key]:
                d[key].pop(); return True
        return False
    return _fit(out, trim)


def project_ack(result, config, inventory_runners, target_ids):
    """runners_verify: collection acknowledgement with meta scoped to the verified target."""
    sources = subject_sources(config, inventory_runners, target_ids)
    out = dict(result, meta=scoped_meta(result.get('meta', {}), sources))
    return _fit(out, lambda r: False)


_ROW_KEYS = ('id', 'display_name', 'scope_id', 'host_id', 'registration', 'activity', 'readiness', 'freshness', 'repair')


def project_list(result, limit_bytes=CAP_BYTES):
    """runners_list: counts plus compact rows, unhealthy first; failed-check detail only where it fails."""
    data = result['data']; meta = result['meta']
    rows = []
    for r in data['runners']:
        row = {k: r.get(k) for k in _ROW_KEYS}
        e = r.get('readiness_evidence')
        if e and (e.get('failed') or e.get('warned')):
            row['readiness_evidence'] = {k: e.get(k) for k in ('failed', 'warned', 'hold_explained_failures', 'observed_at', 'freshness')}
        held = [i for i in r.get('operational_evidence') or [] if i.get('state') != 'clear']
        if held:
            row['operational_evidence'] = held
        rows.append(row)

    def bad(row):
        return not (row['registration'] == 'online' and row['readiness'] == 'pass' and row['freshness'] == 'fresh')
    rows.sort(key=lambda row: (not bad(row), row['id']))
    states = {}
    for p in meta.get('provenance', []):
        states[p.get('state')] = states.get(p.get('state'), 0) + 1
    m = {k: meta[k] for k in _META_KEYS if k in meta}
    m['coverage'] = meta.get('coverage', [])[:50]
    m['provenance_states'] = states
    out = dict(meta=m, data=dict(state=data['state'], collection=data.get('collection'), counts=data['counts'],
                                 total_runners=len(rows), runners=rows))

    def trim(r):
        d = r['data']
        if d['runners']:
            d['runners'].pop(); return True
        return False
    out['truncated'] = False
    while size(out) > limit_bytes and trim(out):
        out['truncated'] = True
    out['data']['returned_runners'] = len(out['data']['runners'])
    return out
