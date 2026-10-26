"""Cached runner tools and diagnosis-only owner-runtime repair operations."""
import json
from pathlib import Path


def tool_principal(session_id, profile):
    """Require the actual in-process session, not a caller-supplied principal."""
    from tui_gateway import server
    if not session_id:
        raise NotImplementedError('owning_gateway_runtime_required')
    home = server._profile_home(profile)
    expected_home = str(home) if home is not None else None
    with server._sessions_lock:
        records = [r for sid, r in server._sessions.items()
                   if (sid == session_id or r.get('session_key') == session_id
                       or getattr(r.get('agent'), 'session_id', None) == session_id)
                   and r.get('profile_home') == expected_home]
        if len(records) != 1:
            raise NotImplementedError('owning_gateway_runtime_required')
        record = records[0]
        principal = server._session_auth_user_id(record)
    if not principal:
        raise PermissionError('authenticated_principal_required')
    return principal


def register(ctx):
    # Schema loading only: no inventory reads, DB creation or background work.
    schema = json.loads((Path(__file__).parent / 'contracts/runners.schema.json').read_text())
    operations = dict(runners_list=('EmptyRequest','inventory'), runners_inspect=('InspectRequest','inspect'),
        runners_refresh=('RefreshRequest','refresh'), runners_repair=('RepairRequest','repair'),
        runners_verify=('VerifyRequest','verify'))
    for name, (request_type, operation) in operations.items():
        description = ('Read cached runner evidence with freshness and provenance. ' if operation in ('inventory','inspect','refresh') else
            'Diagnosis-only runner repair on the owning gateway. No restart, cleanup or job cancellation. '
            'Requires a principal-bearing in-process gateway session; CLI/isolated runtimes fail explicitly. ')
        def handler(args, _op=operation, **context):
            from .dashboard import plugin_api as api
            try:
                backend = api.service()
                if backend.config['owner_profile'] != api.active_profile():
                    raise PermissionError('owner_profile_mismatch')
                from .models import validate
                if _op == 'inventory':
                    validate('EmptyRequest', args)
                    result = backend.inventory()
                elif _op == 'inspect':
                    result = backend.inspect(**validate('InspectRequest', args))
                elif _op == 'refresh':
                    result = backend.refresh(args)
                else:
                    principal = tool_principal(context.get('session_id'), backend.config['owner_profile'])
                    domain = api.repair_domain(backend)
                    if _op == 'verify':
                        result = domain.verify(args, principal=principal)
                    else:
                        from hashlib import sha256
                        from uuid import uuid4
                        requester = dict(principal_id='principal_' + sha256(principal.encode()).hexdigest()[:32],
                            connection_id=None, session_id=None, request_id='request_' + uuid4().hex)
                        repair = domain.reserve(args, requester, principal=principal)
                        repair = domain.start(repair['id'], principal=principal)
                        result = dict(meta=backend.inventory()['meta'], data=dict(repair=repair, owner_route=None, route_status='unresolved'))
                return json.dumps(result)
            except (PermissionError, NotImplementedError) as exc:
                reason = str(exc)
                return json.dumps(dict(error=reason if reason in ('owning_gateway_runtime_required', 'owner_profile_mismatch', 'authenticated_principal_required') else 'repair_access_denied'))
            except (RuntimeError, ValueError, KeyError, OSError, ImportError):
                return json.dumps(dict(error='runner_operation_unavailable_or_unauthorized'))
        parameters = dict(schema['$defs'][request_type], **{'$defs': schema['$defs']})
        ctx.register_tool(name=name, toolset='runners', description=description,
            schema=dict(name=name, description=description, parameters=parameters), handler=handler)
