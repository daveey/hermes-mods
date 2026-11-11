"""An older core without fenced transitions must not get clickable card tiles.

The plugin can reach a host before the core that adds
``unblock_task/archive_task(precondition=, comment=, event_extra=)``. Publishing a
tile there would accept a click and then fail delivery, so projection waits.
"""

from types import SimpleNamespace

from test_kanban_mirror import fixture, OWNER


def old_core(api):
    # Pre-fence core signatures: no precondition/comment/event_extra keywords.
    def unblock_task(conn, task_id, *, reason=None):
        raise AssertionError("must not be called")

    def archive_task(conn, task_id):
        raise AssertionError("must not be called")

    return SimpleNamespace(**dict(vars(api), unblock_task=unblock_task, archive_task=archive_task))


def test_old_core_publishes_no_card_tile(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.backend = old_core(mirror.backend)
    mirror.reconcile(OWNER)
    assert store.requests(OWNER) == []


def test_fenced_core_publishes_card_tile(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    [request] = store.requests(OWNER)
    assert [c["label"] for c in request["choices"]] == ["Red", "Green", "Blue"]


def test_explicit_fenced_signature_is_supported(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    api = mirror.backend

    def unblock_task(conn, task_id, *, precondition=None, comment=None, event_extra=None):
        return api.unblock_task(conn, task_id, precondition=precondition, comment=comment,
                                event_extra=event_extra)

    def archive_task(conn, task_id, *, precondition=None, comment=None, event_extra=None):
        return api.archive_task(conn, task_id, precondition=precondition, comment=comment,
                                event_extra=event_extra)

    mirror.backend = SimpleNamespace(**dict(vars(api), unblock_task=unblock_task,
                                            archive_task=archive_task))
    mirror.reconcile(OWNER)
    assert len(store.requests(OWNER)) == 1
