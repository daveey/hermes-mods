"""Create a class-routed repair consumer through the canonical gateway.

The legacy titled CLI create path discards launch selectors; resume cannot change
its frozen policy. Use a new, stable titled session instead of mutating a running
or historical consumer. All action/delivery identities remain in the same ledgers.
"""
from __future__ import annotations

TITLE = 'Session repair (routed)'
MODEL = 'class:standard'


def prepare(store=None) -> str:
    if store is None:
        from hermes_cli.gateway_connection import GatewayConnection
        from hermes_cli.gateway_session_store import GatewaySessionStore
        store = GatewaySessionStore(connection=GatewayConnection())
    try:
        sid = store.create({'model': MODEL, 'provider': 'auto'}, title=TITLE, record_cwd=False)
        # Resolve-or-create can return an existing title. Never trust its title as
        # routing evidence or mutate it (it may have a running turn).
        snapshot = store.call('session.resume', session_id=sid)
        launch = (snapshot.get('info') or {}).get('launch_request') or {}
        if (launch.get('model') != MODEL or launch.get('provider') not in (None, '', 'auto')
                or launch.get('base_url') or launch.get('api_key')):
            raise ValueError('repair consumer does not have the required class-routed launch policy')
        if snapshot.get('stored_session_id') != sid:
            raise ValueError('repair consumer identity changed on readback')
        return sid
    finally:
        store.close_reader()
        store.connection.close()
