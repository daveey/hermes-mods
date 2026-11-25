"""Read-only adapters for the panel reducer's extra inputs. Canonical ask
reconciliation belongs to source admission, not presentation reads.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import sys
from pathlib import Path

log = logging.getLogger('session-panel')


def _obj(value):
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(value or '{}')
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def process_inputs(session, lineage, profile):
    """Capture actual process probes, never renew a carried observation's lease."""
    annotated = {'id': session, 'profile': profile, '_lineage_ids': list(lineage)}
    producer = importlib.import_module('hermes_cli.process_session_activity')
    # A checkpoint birth can arrive inside the sidebar adapter's three-second
    # cache. This admitted producer pass must probe, not certify that old cache.
    producer._invalidate_cache()
    producer.annotate_background_activity([annotated])
    details = {r.get('id'): r for r in annotated.get('background_processes') or []}
    items = []
    for obs in annotated.get('execution_observations') or []:
        identity = _obj(obs.get('identity'))
        if identity.get('kind') != 'process':
            continue
        wid = identity.get('work_id')
        if not wid:
            raise ValueError('process inventory identity unavailable')
        items.append({'id': wid, 'work_id': wid, 'gateway': identity.get('gateway'),
                      'profile': identity.get('profile'), 'execution_observations': [obs],
                      'title': details.get(wid, {}).get('command') or 'Background process'})
    return items


def read(reader, session, lineage):
    out = {'outcome_json': {}, 'requests': [], 'subagents': [], 'processes': [], 'rentals': [], 'input_errors': []}
    db = reader.conn(reader.state_db())
    home = reader.state_db().parent
    known_absent = False
    try:
        row = db.execute('SELECT outcome_json FROM sessions WHERE id=?', (session,)).fetchone() if db else None
        known_absent = db is not None and row is None
        out['outcome_json'] = _obj(row['outcome_json']) if row else {}
    except Exception:
        out['input_errors'].append('session outcome unavailable')
        log.debug('panel outcome unavailable', exc_info=True)
    # Read the current frozen revision, but overlay its CURRENT lifecycle. A revision
    # body still says open after withdrawal; reading body alone resurrects old asks.
    profile = home.name if home.parent.name == 'profiles' else 'default'
    # Like the fast lane, a successfully queried absent own row has no
    # request authority. Do not resurrect surviving store rows for deleted chats.
    canonical = [] if known_absent else None
    if not known_absent and hasattr(reader, 'user_step_requests'):
        try:
            canonical = reader.user_step_requests(session, lineage)
        except Exception:
            if getattr(reader, 'home_context', None) is not None:
                raise  # explicit refusal cannot become a raw-store fallback
            out['input_errors'].append('user-steps requests unavailable')
            log.debug('panel canonical requests unavailable', exc_info=True)
    store = reader.conn(home / 'user-steps' / 'actions.sqlite3') if canonical is None else None
    out['profile'] = profile
    explicit = getattr(reader, 'home_context', None) is not None
    if explicit:
        ident = reader._home_identity()
        out['gateway'] = ident.get('gateway') or ''
        install = ident.get('install_id')
        out['gateway_aliases'] = [f'install:{install}'] if install else []
    else:
        try:
            out['gateway'] = reader.ksc.load_identity().gateway
            # user-steps owns local requests as `install:<id>`: the same gateway under its other name.
            try:
                from hermes_cli.install_identity import get_install_id
                install = get_install_id()
                out['gateway_aliases'] = [f'install:{install}'] if install else []
            except Exception:
                out['gateway_aliases'] = []
        except (AttributeError, OSError, ValueError, RuntimeError):
            # Core raises SyncUnavailable (RuntimeError) on standalone/unenrolled
            # installs. Missing hub identity must not discard the local panel inputs.
            out['gateway'] = ''
    if canonical is not None:
        out['requests'] = canonical
    elif store is not None:
        try:
            marks = ','.join('?' for _ in lineage)
            rows = store.execute('SELECT r.state,r.resolution,v.body FROM requests r JOIN revisions v '
                                 'ON r.namespace=v.namespace AND r.id=v.id AND r.revision=v.revision '
                                 f'WHERE r.session IN ({marks}) ORDER BY r.rowid', tuple(lineage)).fetchall()
            for row in rows:
                body = _obj(row['body'])
                body.update(state=row['state'], resolution=_obj(row['resolution']) if row['resolution'] else None)
                # The path is profile-scoped and owner is still verified against its own row.
                owner = _obj(body.get('owner'))
                if owner.get('storedSessionId') in lineage and owner.get('profile', 'default') == profile:
                    out['requests'].append(body)
        except Exception:
            out['input_errors'].append('user-steps requests unavailable')
            log.debug('panel requests unavailable', exc_info=True)
    profile = home.name if home.parent.name == 'profiles' else 'default'
    annotated = {'id': session, 'profile': profile, '_lineage_ids': list(lineage)}
    if explicit:
        out['input_errors'].extend(['subagent inventory adapter unavailable',
                                    'process inventory adapter unavailable',
                                    'rental inventory adapter unavailable'])
    else:
        for module_name, fn_name in (('hermes_cli.subagent_session_activity', 'annotate_subagent_activity'),
                                      ('hermes_cli.process_session_activity', 'annotate_background_activity')):
            try:
                getattr(importlib.import_module(module_name), fn_name)([annotated])
            except Exception:
                out['input_errors'].append(module_name.rsplit('.', 1)[-1] + ' unavailable')
                log.debug('panel runtime unavailable', exc_info=True)
        observations = annotated.get('execution_observations') or []
        processes = {r.get('id'): r for r in annotated.get('background_processes') or []}
        for obs in observations:
            identity = _obj(obs.get('identity'))
            kind, wid = identity.get('kind'), identity.get('work_id')
            if kind not in ('subagent', 'process'):
                continue
            if not wid:
                out['input_errors'].append(f'{kind} inventory unknown: {obs.get("unknown_reason") or "missing identity"}')
            item = {'id': wid or f'{kind}-unknown', 'work_id': wid, 'gateway': identity.get('gateway'),
                    'profile': identity.get('profile'), 'execution_observations': [obs]}
            if kind == 'subagent' and db is not None:
                try:
                    row = db.execute('SELECT title FROM sessions WHERE id=?', (wid,)).fetchone()
                except Exception:
                    row = None
                    out['input_errors'].append('subagent title unavailable')
                item['title'] = row['title'] if row and row['title'] else 'Sub-agent'
            else:
                item['title'] = processes.get(wid, {}).get('command') or 'Background process'
            out['subagents' if kind == 'subagent' else 'processes'].append(item)
        # Reuse the rentals API's shared service/snapshot, not another cloud collector.
        try:
            name = '_session_panel_rentals_api'
            api = sys.modules.get(name)
            if api is None:
                path = Path(__file__).resolve().parents[1] / 'compute-rentals' / 'dashboard' / 'plugin_api.py'
                spec = importlib.util.spec_from_file_location(name, path)
                assert spec is not None and spec.loader is not None
                api = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(api)
                api = sys.modules.setdefault(name, api)
            service = api._plugin().get_service()
            service.start_refresh()
            for sid in lineage:
                inventory = service.inventory(session_id=sid, profile=profile, refresh=False)
                for machine in inventory.get('machines') or []:
                    if any(o.get('gateway_id') == inventory.get('gateway_id') and o.get('profile') == profile and o.get('session_id') == sid for o in machine.get('ownership') or []):
                        out['rentals'].append({**machine, 'id': machine['key'], 'title': machine.get('display_name') or machine.get('provider_id') or 'machine'})
        except Exception:
            out['input_errors'].append('rentals unavailable')
            log.debug('panel rentals unavailable', exc_info=True)
    return out

