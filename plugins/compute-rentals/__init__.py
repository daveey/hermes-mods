"""Compute rentals: read-only cloud inventory, local session bindings.

Tool handlers receive trusted session_id from Hermes' registry when available.
It overrides model-supplied session_id. The public PluginContext.profile_name
accessor supplies the active profile on supported hosts; older hosts require it
explicitly. gateway_id remains explicit. No stack inspection, routing-key
inference, cloud tags, or lifecycle mutations are used. Explicit REST identities
are not validated against the host session database.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading

# A plugin hot-reload re-executes this file; drop cached siblings so edited
# inventory/ownership/autolink code is picked up instead of stale copies.
for _stale in ('_compute_rentals_inventory', '_compute_rentals_ownership', '_compute_rentals_autolink'):
    sys.modules.pop(_stale, None)


def _inventory_module():
    name = '_compute_rentals_inventory'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / 'inventory.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name,None)
            raise
    return sys.modules[name]


_services = {}
_service_lock = threading.RLock()


def discover_adapters(service, root=None):
    """Explicit extension point: providers/__init__.py:build_collectors() ->
    mapping of provider IDs to zero-argument collect callables. The factory must
    not perform cloud calls; absent package means unconfigured, not connected.
    Legacy create_adapters() is supported when build_collectors is absent.
    Relative imports work under an isolated synthetic package name. Never scan
    or import arbitrary modules from sys.path.
    """
    import hashlib
    root = Path(root or Path(__file__).parent).resolve()
    path = root / 'providers' / '__init__.py'
    if not path.is_file():
        return
    name = '_compute_rentals_providers_' + hashlib.sha256(str(root).encode()).hexdigest()[:16]
    try:
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(name,path,submodule_search_locations=[str(path.parent)])
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        module = sys.modules[name]
        factory = getattr(module, 'build_collectors', None) or getattr(module, 'create_adapters')
        adapters = factory()
        if not isinstance(adapters, dict) or any(not callable(c) for c in adapters.values()):
            raise TypeError('factory must return provider/callable mapping')
        for provider, collect in adapters.items():
            service.register_adapter(provider,collect)
        labels = getattr(module, 'LABELS', None)
        if isinstance(labels, dict):
            service.labels.update({k: v for k, v in labels.items() if isinstance(k, str) and isinstance(v, str)})
    except Exception:
        import logging
        logging.getLogger(__name__).exception('compute-rentals: provider factory failed')
        sys.modules.pop(name,None)
        def unavailable():
            raise RuntimeError('Adapter factory failed')
        for provider in service.providers:
            service.register_adapter(provider,unavailable)


def get_service():
    try:
        from hermes_constants import get_hermes_home
        home = Path(get_hermes_home()).resolve()
    except ImportError:
        home = Path(os.environ.get('HERMES_HOME', '~/.hermes')).expanduser().resolve()
    with _service_lock:
        if home not in _services:
            service = _inventory_module().InventoryService(home / 'compute-rentals')
            discover_adapters(service)
            _services[home] = service
        return _services[home]


def _find(service, provider, provider_id):
    with service._lock:
        hits = [m for p, ms in service._machines.items() if not provider or p == provider
                for m in ms.values() if m.get('provider_id') == provider_id]
    if len(hits) > 1:
        raise ValueError('provider_id is ambiguous across providers; pass provider')
    return hits[0]['key'] if hits else None


def resolve_machine_key(service, provider, provider_id, attempts=4, delay=3.0, sleep=None):
    """Map (provider, provider_id) -- what a rent command returns -- to a machine key.

    A freshly rented machine may not be in the cached snapshot yet, so on a
    miss drop provider-side caches (e.g. EC2's 5-minute cache) and force a
    refresh, retrying briefly while the provider API catches up.
    """
    import time as _time
    sleep = sleep or _time.sleep
    provider_id = str(provider_id).strip()
    if not provider_id:
        raise ValueError('provider_id must be nonempty')
    for attempt in range(attempts):
        key = _find(service, provider, provider_id)
        if key:
            return key
        if attempt:
            sleep(delay)
        for name, collect in list(service.adapters.items()):
            owner = getattr(collect, '__self__', None)
            if (not provider or name == provider) and owner is not None and hasattr(owner, '_cached'):
                owner._cached = None
        with service._lock:
            service._last_refresh = None
        service.inventory(refresh=True)
    raise LookupError('%s machine %s not found in inventory after refresh' % (provider or 'any provider', provider_id))


def binding(service, action, payload):
    payload = dict(payload)
    if not payload.get('gateway_id'):
        payload['gateway_id'] = service.gateway_id
    if not payload.get('machine_key') and payload.get('provider_id'):
        payload['machine_key'] = resolve_machine_key(service, payload.get('provider'), payload['provider_id'])
    values = {k:payload.get(k) for k in ('machine_key','gateway_id','profile','session_id')}
    if values['gateway_id'] != service.gateway_id:
        raise ValueError('gateway_id must match this backend inventory gateway_id')
    service.ownership._values(values['machine_key'],values['gateway_id'],values['profile'],values['session_id'])
    if action == 'attach' and not any(m['key'] == values['machine_key'] for m in service.inventory()['machines']):
        raise LookupError('machine_key not present in inventory; refresh first')
    owners = getattr(service.ownership, action)(**values)
    return dict(machine_key=values['machine_key'], ownership=owners)


def _autolink_module():
    name = '_compute_rentals_autolink'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / 'autolink.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


def _auto_link(service, session_id, profile):
    """Attach only machines this session (or its subagents) demonstrably used.

    See autolink.py. Never links every running machine in the account.
    """
    try:
        return _autolink_module().auto_link(service, session_id, profile)
    except Exception:
        return []


def register(ctx):
    for operation in ('list','attach','detach'):
        def handler(args, _op=operation, **context):
            try:
                payload = dict(args)
                for field in ('session_id','gateway_id','profile'):
                    if context.get(field):
                        payload[field] = context[field]
                host_profile = getattr(ctx, 'profile_name', None)
                if host_profile is not None:
                    _inventory_module().ownership_module.identity_value(host_profile)
                    payload['profile'] = host_profile
                service = get_service()
                if _op == 'list':
                    # No implicit linking: the rent-machine skill calls
                    # compute_rentals_attach for every rental (explicit owner).
                    result = service.inventory(**{k:v for k,v in payload.items() if k in ('refresh','session_id','gateway_id','profile')})
                else:
                    result = binding(service, _op, payload)
                return json.dumps(result)
            except (ValueError, LookupError) as exc:
                return json.dumps(dict(error=str(exc)))
        properties = {k:dict(type='string') for k in ('gateway_id','profile','session_id')}
        if operation == 'list':
            properties['refresh'] = dict(type='boolean', default=False)
        else:
            properties['machine_key'] = dict(type='string', description='Canonical inventory key; optional when provider_id is given.')
            properties['provider'] = dict(type='string', description='Provider id as listed by compute_rentals_list (e.g. vast, runpod, lambda, ec2, sfcompute, modal, primeintellect).')
            properties['provider_id'] = dict(type='string', description='Provider instance/pod/node id as returned by the rent call (e.g. Vast new_contract, RunPod pod id, EC2 i-...).')
        name = 'compute_rentals_' + operation
        if operation == 'list':
            description = 'Read-only global compute inventory and session totals.'
        elif operation == 'attach':
            description = ('Link a rented machine to the CURRENT session (local registry only; never changes cloud machines). '
                           'Call immediately after renting any machine: pass provider + provider_id from the rent response. '
                           'Refreshes inventory and retries if the new machine is not visible yet.')
        else:
            description = 'Unlink a machine from the current session (local registry only; never changes cloud machines). Pass provider + provider_id or machine_key.'
        description += ' Session/profile/gateway are filled from the trusted current session.'
        schema = dict(name=name, description=description, parameters=dict(type='object', properties=properties, required=[], additionalProperties=False))
        ctx.register_tool(name=name, toolset='compute_rentals', schema=schema, handler=handler, description=description)
