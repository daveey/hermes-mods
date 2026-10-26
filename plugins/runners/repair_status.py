"""Collector-only core bridge: status reads, never runtime lookup or dispatch.

SessionDB/StartStore may initialize their additive schema; status itself only
reads canonical durable state. A missing owner database is not a terminal receipt.
"""
from contextlib import contextmanager
from pathlib import Path


class _NoRuntime:
    def check_supported(self, owner):
        raise NotImplementedError('collector_status_only')

    def schedule(self, row, intent, finish):
        raise NotImplementedError('collector_status_only')


class StatusOnlyCore:
    """Expose only registration and authoritative status, not start/claim/finish."""
    def __init__(self, db, gateway_id, profile):
        from tui_gateway.session_start_requests import StartStore, StartService
        self.gateway_id, self.profile = gateway_id, profile
        self.store = StartStore(db)
        self._service = StartService(self.store, _NoRuntime())

    def _check(self, owner):
        if (owner.gateway_id, owner.profile) != (self.gateway_id, self.profile):
            raise ValueError('owner_conflict')

    def register(self, owner, namespace, resolver):
        self._check(owner)
        return self._service.register(owner, namespace, resolver)

    def status(self, owner, namespace, request_id):
        self._check(owner)
        return self._service.status(owner, namespace, request_id)


@contextmanager
def repair_reconciler(service):
    """Open exactly the configured owner's canonical SessionDB, never plugin DB."""
    from hermes_cli.profiles import get_profile_dir
    from hermes_state import SessionDB
    from .repairs import RepairService

    profile = service.config['owner_profile']
    path = Path(get_profile_dir(profile)) / 'state.db'
    if not path.is_file():
        raise RuntimeError('repair_session_store_unavailable')
    db = SessionDB(db_path=path)
    repairs = None
    try:
        core = StatusOnlyCore(db, service.config['owner_gateway_id'], profile)

        def factory(requested):
            if requested != profile:
                raise ValueError('owner_conflict')
            return core

        repairs = RepairService(service, core_factory=factory)
        yield repairs
    finally:
        try:
            if repairs is not None:
                repairs.close()
        finally:
            db.close()
