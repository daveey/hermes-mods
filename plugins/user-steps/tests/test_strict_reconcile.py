"""Event-driven callers cannot certify deferred producer work as complete."""
import pytest
from test_decisions import OWNER
from test_kanban_mirror import fixture


def test_strict_reconcile_propagates_remote_deferred_and_recovers(tmp_path, monkeypatch):
    mirror, store, _, _ = fixture(tmp_path)
    monkeypatch.setattr(mirror.remote, 'snapshots', lambda owner: iter([{'unknown': ('g', 'p', 'b', 't')}]))
    with pytest.raises(RuntimeError, match='Remote card reconciliation deferred'):
        mirror.reconcile(OWNER, strict=True)
    monkeypatch.setattr(mirror.remote, 'snapshots', lambda owner: iter([]))
    mirror.reconcile(OWNER, strict=True)
    assert store.views(OWNER)


def test_strict_reconcile_propagates_local_deferred_without_changing_legacy_api(tmp_path, monkeypatch):
    mirror, _, _, _ = fixture(tmp_path)
    monkeypatch.setattr(mirror.remote, 'snapshots', lambda owner: iter([]))
    monkeypatch.setattr(mirror, '_key', lambda *args: None)
    monkeypatch.setattr(mirror, '_reconcile', lambda *args: False)
    mirror.reconcile(OWNER)  # legacy callers still accept partial presentation
    with pytest.raises(RuntimeError, match='Card reconciliation deferred'):
        mirror.reconcile(OWNER, strict=True)
