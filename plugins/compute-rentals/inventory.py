"""Read-only inventory aggregation.

Adapter contract: register_adapter(provider, collect), where collect() returns
{provider, machines, status: ok|partial|error|unconfigured, error, scope, observed_at}.
Identity is (provider, account, region, provider_id), all nonempty strings. Regions
without a geographic meaning should use an explicit provider-defined value.
Rates are bounded decimal strings, never floats; missing rates remain null. Machine
timestamps are timezone-aware ISO-8601; provider observed_at may be null. Optional
text metadata (ssh, console_url, rate_note, state_detail, scope, error) is string or
null, never raw objects. Adapter code owns read-only calls and safe error text.
last_success_at is the local completion time of the last fully successful collection,
retained across partial/error results and restarts; it is not a provider observation.
persistence_error reports a failed local cache write without losing fresh memory.
"""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import importlib.util
import json
from pathlib import Path
import sys
import threading
import uuid
import time


def load_sibling(name):
    """Load only a named sibling without altering sys.path (disk plugin has a hyphen)."""
    path = Path(__file__).resolve().parent / (name + '.py')
    module_name = '_compute_rentals_' + name
    if module_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
    return sys.modules[module_name]


ownership_module = load_sibling('ownership')


def now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp must be a string')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('timestamp must include time and timezone')
    return value


def optional_text(value, field):
    if value is not None and not isinstance(value, str):
        raise ValueError(field + ' must be a string or null')
    return value


MACHINE_KINDS = ('instance', 'physical_node', 'pod', 'serverless_worker', 'sandbox')


def normalize_machine(raw, provider):
    item = dict(raw)
    parts = [item.get(k) for k in ('provider', 'account', 'region', 'provider_id')]
    for part in parts:
        ownership_module.identity_value(part)
    if parts[0] != provider:
        raise ValueError('adapter provider mismatch')
    out = dict(zip(('provider','account','region','provider_id'), parts))
    out['resource_kind'] = item.get('resource_kind', 'instance')
    if out['resource_kind'] not in MACHINE_KINDS:
        raise ValueError('invalid machine resource kind')
    out['billing_class'] = item.get('billing_class', 'unknown')
    if out['billing_class'] not in ('unknown', 'rental', 'internal_prepaid'):
        raise ValueError('invalid billing class')
    if out['billing_class'] == 'internal_prepaid' and (out['resource_kind'] != 'physical_node' or item.get('hourly_rate') is not None):
        raise ValueError('internal prepaid physical nodes require null rental rate')
    out['key'] = json.dumps(parts, ensure_ascii=False, separators=(',', ':'))
    if 'key' in item and item['key'] != out['key']:
        raise ValueError('machine key does not match identity')
    for field, default in (('display_name',parts[3]), ('status','unknown'), ('hardware',''), ('started_at',None), ('expires_at',None), ('console_url',None), ('ssh',None), ('observed_at',now())):
        out[field] = item.get(field, default)
    for field in ('display_name','status','hardware'):
        if not isinstance(out[field],str):
            raise ValueError(field + ' must be a string')
    for field in ('started_at','expires_at','observed_at'):
        value = out[field]
        if value is None and field != 'observed_at':
            continue
        timestamp(value)
    for field in ('ssh', 'console_url'):
        optional_text(out[field], field)
    value = item.get('hourly_rate')
    rate = None
    # Bound parsing and fixed-point expansion without restricting ordinary rates.
    if isinstance(value, str) and len(value) <= 4096:
        try:
            number = Decimal(value)
            if number.is_finite() and number >= 0 and abs(number.as_tuple().exponent) <= 4096 and number.adjusted() <= 4096:
                rate = format(number, 'f')
        except InvalidOperation:
            pass
    currency = item.get('currency')
    out['currency'] = currency if isinstance(currency,str) and currency else None
    out['hourly_rate'] = rate if out['currency'] else None
    out['rate_basis'] = item.get('rate_basis','unknown') if rate is not None else 'unknown'
    if out['rate_basis'] not in ('reported','estimated','unknown'):
        out['rate_basis'] = 'unknown'
    for field in ('rate_note', 'state_detail'):
        out[field] = optional_text(item.get(field), field)
    out['stale'] = False
    return out


WORKLOAD_KINDS = ('allocation', 'queued_job', 'container')


def bounded_list(value, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError('expected bounded list')
    return value


def workload_text(value, field, limit=4096, nullable=True):
    optional_text(value, field)
    if (value is None and not nullable) or (value is not None and len(value) > limit):
        raise ValueError('invalid workload ' + field)
    return value


def normalize_workload(raw, provider):
    if not isinstance(raw, dict):
        raise ValueError('workload must be an object')
    parts = [raw.get(k) for k in ('provider', 'account', 'provider_id')]
    for part in parts:
        ownership_module.identity_value(part)
        workload_text(part, 'identity', limit=1024, nullable=False)
    if parts[0] != provider:
        raise ValueError('adapter provider mismatch')
    out = dict(zip(('provider', 'account', 'provider_id'), parts))
    out['key'] = json.dumps([parts[0], parts[1], 'internal', parts[2]], ensure_ascii=False, separators=(',', ':'))
    if 'key' in raw and raw['key'] != out['key']:
        raise ValueError('workload key does not match identity')
    kind = raw.get('resource_kind')
    if kind not in WORKLOAD_KINDS:
        raise ValueError('invalid workload kind')
    out['resource_kind'] = kind
    for field, default in (('display_name', parts[2]), ('status', 'unknown'),
                           ('hardware', None), ('state_detail', None)):
        out[field] = workload_text(raw.get(field, default), field, nullable=field not in ('display_name', 'status'))
    for field in ('started_at', 'expires_at', 'observed_at'):
        value = workload_text(raw.get(field, now() if field == 'observed_at' else None), field, limit=128)
        out[field] = timestamp(value) if value is not None or field == 'observed_at' else None
    parents = bounded_list(raw.get('parent_machine_keys', []), 1024)
    out['parent_machine_keys'] = sorted(set(ownership_module.validate_key(workload_text(k, 'parent key', limit=8192, nullable=False)) for k in parents))
    out['stale'] = False
    return out


def summarize_workloads(workloads, complete):
    counts = dict.fromkeys(WORKLOAD_KINDS, 0)
    active = dict.fromkeys(WORKLOAD_KINDS, 0)
    unknown = 0
    for item in workloads:
        counts[item['resource_kind']] += 1
        status = item['status']
        if status in ('running', 'starting', 'queued', 'pending', 'suspended', 'stopping'):
            active[item['resource_kind']] += 1
        elif status not in ('stopped', 'completed', 'failed', 'cancelled', 'terminated', 'exited'):
            unknown += 1
    return dict(count=len(workloads), by_resource_kind=counts, active_by_resource_kind=active,
                unknown_status_count=unknown,
                complete=complete and not any(w['stale'] for w in workloads))


def normalize_state(result, provider):
    if result.get('provider') != provider or result.get('status') not in ('ok', 'partial', 'error', 'unconfigured'):
        raise ValueError('invalid adapter result')
    state = {k: result.get(k) for k in ('provider', 'status', 'error', 'scope', 'observed_at')}
    for field in ('error', 'scope'):
        optional_text(state[field], field)
    if state['observed_at'] is not None:
        timestamp(state['observed_at'])
    return state


def summarize(machines, complete):
    rates = {}
    unknown = 0
    internal = 0
    kinds = dict.fromkeys(MACHINE_KINDS, 0)
    for item in machines:
        kinds[item['resource_kind']] += 1
        if item['billing_class'] == 'internal_prepaid':
            internal += 1
            continue
        if item['hourly_rate'] is None:
            unknown += 1
        else:
            currency = item['currency']
            left = rates.get(currency, Decimal(0))
            right = Decimal(item['hourly_rate'])
            # Align both coefficients and reserve one digit for a carry. This is
            # exact irrespective of the caller's decimal context precision.
            with localcontext() as context:
                context.prec = max(left.adjusted(), right.adjusted(), 0) - min(left.as_tuple().exponent, right.as_tuple().exponent, 0) + 2
                rates[currency] = left + right
    return dict(count=len(machines), by_resource_kind=kinds, internal_count=internal,
                rental_count=len(machines)-internal,
                rates={k:format(v,'f') for k,v in rates.items()}, unknown_rates=unknown,
                complete=complete and unknown == 0 and not any(m['stale'] for m in machines))


class InventoryService:
    def __init__(self, state_dir, providers=('vast','sfcompute','ec2'), clock=time.monotonic):
        self.state_dir = Path(state_dir)
        self.ownership = ownership_module.OwnershipStore(self.state_dir / 'registry.db')
        self.adapters = {}
        self.providers = dict.fromkeys(providers)
        self.labels = {}  # provider id -> display name, from the provider factory
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._clock = clock
        self._last_refresh = None
        self._refreshing = False
        self._machines = {}
        self._workloads = {}
        self._states = {}
        with self.ownership.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), gateway_id TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO identity VALUES (1,?)', ('compute-rentals:' + str(uuid.uuid4()),))
            self.gateway_id = db.execute('SELECT gateway_id FROM identity WHERE singleton=1').fetchone()[0]
            db.execute('CREATE TABLE IF NOT EXISTS inventory_cache (provider TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            for provider, payload in db.execute('SELECT provider,payload FROM inventory_cache'):
                if provider in self.providers:
                    self._load_cache(provider, payload)

    def _load_cache(self, provider, payload):
        # Older cache versions may contain arbitrary adapter objects. Treat disk
        # data as another input boundary, not as prevalidated normalized data.
        try:
            cached = json.loads(payload)
            state = normalize_state(cached['state'], provider)
            last_success = cached['state'].get('last_success_at')
            if last_success is not None:
                timestamp(last_success)
            items = [normalize_machine(m, provider) for m in cached['machines']]
            workloads = [normalize_workload(w, provider) for w in bounded_list(cached.get('workloads', []), 10000)]
            state.update(last_success_at=last_success, persistence_error=None,
                         status='partial', error='Cached inventory; refresh required')
        except Exception:
            items = []
            workloads = []
            state = dict(provider=provider, status='error', error='Invalid inventory cache; refresh required',
                         scope=None, observed_at=None, last_success_at=None, persistence_error=None)
        self._machines[provider] = {m['key']: dict(m, stale=True) for m in items}
        self._workloads[provider] = {w['key']: dict(w, stale=True) for w in workloads}
        self._states[provider] = state

    def _refresh(self, provider, collect):
        previous = self._states.get(provider, {})
        try:
            result = collect()
            state = normalize_state(result, provider)
            status = state['status']
            items = [normalize_machine(m, provider) for m in result.get('machines', [])] if status in ('ok','partial') else []
            workloads = [normalize_workload(w, provider) for w in bounded_list(result.get('workloads', []), 10000)] if status in ('ok','partial') else []
        except Exception:
            items = []
            workloads = []
            status = 'error'
            state = dict(provider=provider, status=status, error='Collection failed', scope=previous.get('scope'), observed_at=now())
        state['last_success_at'] = now() if status == 'ok' else previous.get('last_success_at')
        with self._lock:
            old = {k:dict(v, stale=True) for k,v in self._machines.get(provider,{}).items()}
            current = {m['key']:m for m in items}
            self._machines[provider] = current if status == 'ok' else {**old, **current}
            old_workloads = {k:dict(v, stale=True) for k,v in self._workloads.get(provider, {}).items()}
            current_workloads = {w['key']:w for w in workloads}
            self._workloads[provider] = current_workloads if status == 'ok' else {**old_workloads, **current_workloads}
            self._states[provider] = state
            payload = json.dumps(dict(machines=list(self._machines[provider].values()), workloads=list(self._workloads[provider].values()), state=state))
        # Publish the snapshot before persistence; external SQLite writers must not
        # hold up readers or discard a successfully collected in-memory snapshot.
        try:
            with self.ownership.connect(timeout=0.1) as db:
                db.execute('INSERT OR REPLACE INTO inventory_cache VALUES (?,?)', (provider,payload))
            persistence_error = None
        except Exception:
            persistence_error = 'Inventory cache write failed'
        with self._lock:
            self._states[provider] = dict(state, persistence_error=persistence_error)

    def register_adapter(self, provider, collect):
        if not callable(collect):
            raise TypeError('collect must be callable')
        with self._lock:
            if provider not in self.providers:
                with self.ownership.connect() as db:
                    row = db.execute('SELECT payload FROM inventory_cache WHERE provider=?', (provider,)).fetchone()
                if row:
                    self._load_cache(provider, row[0])
            self.providers[provider] = None
            self.adapters[provider] = collect
            self._last_refresh = None

    def _refresh_if_due(self, force):
        if not self._refresh_lock.acquire(blocking=False):
            return
        try:
            with self._lock:
                if self._last_refresh is not None and self._clock() - self._last_refresh < (2 if force else 30):
                    return
                adapters = list(self.adapters.items())
                self._refreshing = bool(adapters)
            try:
                for provider, collect in adapters:
                    self._refresh(provider, collect)
            finally:
                with self._lock:
                    self._refreshing = False
                    self._last_refresh = self._clock()
        finally:
            self._refresh_lock.release()

    def inventory(self, session_id='', profile='', gateway_id='', refresh=False):
        self._refresh_if_due(refresh)
        with self._lock:
            providers = [dict(self._states.get(p, dict(provider=p, status='loading' if self._refreshing and p in self.adapters else 'unconfigured', error=None, scope=None, observed_at=None, last_success_at=None, persistence_error=None)), label=self.labels.get(p)) for p in self.providers]
            machines = [dict(m, ownership=self.ownership.owners(m['key'])) for p in self.providers for m in self._machines.get(p,{}).values()]
            machines.sort(key=lambda m:m['key'])
            identity = dict(gateway_id=gateway_id or self.gateway_id, profile=profile, session_id=session_id)
            selected = [m for m in machines if session_id and all(identity.values()) and identity in m['ownership']]
            workloads = sorted((dict(w, parent_machine_keys=list(w['parent_machine_keys'])) for p in self.providers for w in self._workloads.get(p, {}).values()), key=lambda w:w['key'])
            selected_keys = {m['key'] for m in selected}
            associated = []
            for workload in workloads:
                parents = sorted(selected_keys.intersection(workload['parent_machine_keys']))
                if parents:
                    associated.append(dict(workload, association=dict(
                        basis='explicit_parent_machine_binding', exclusive=False,
                        parent_machine_keys=parents,
                        label='Associated through a bound machine; not exclusive workload ownership')))
            complete = not self._refreshing and all(p['status'] == 'ok' for p in providers)
            return dict(gateway_id=self.gateway_id, refreshing=self._refreshing,
                        machines=machines, providers=providers,
                        summary=summarize(machines,complete), session_summary=summarize(selected,complete),
                        workloads=workloads, session_workloads=associated,
                        workload_summary=summarize_workloads(workloads,complete),
                        session_workload_summary=summarize_workloads(associated,complete), observed_at=now())
