"""One feed producer pass checks every other owner's binding once, not once per owner it reconciles.

Every owner's reconcile also re-checks all OTHER owners' bindings of the namespace ("older owners may never
reconcile again"), so a pass over N chats checked each binding N times."""
import pytest

from test_kanban_mirror import fixture, OWNER, core

OTHER = dict(OWNER, storedSessionId="session-b")


def counting(mirror, monkeypatch):
    seen = []
    real = mirror._reconcile_binding

    def spy(owner, binding):
        seen.append((owner["storedSessionId"], binding["session"]))
        return real(owner, binding)

    monkeypatch.setattr(mirror, "_reconcile_binding", spy)
    return seen


def test_without_a_pass_every_owner_rechecks_the_other_owners_binding(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    seen = counting(mirror, monkeypatch)
    for _ in range(3):
        mirror.reconcile(OTHER)
    assert seen == [("session-a", "session-a")] * 3


def test_inside_a_pass_an_owners_binding_is_checked_once(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    seen = counting(mirror, monkeypatch)
    mod = type(mirror).reconcile.__globals__
    with mod["reconcile_pass"]():
        for _ in range(3):
            mirror.reconcile(OTHER)
        assert seen == [("session-a", "session-a")]
        mirror.reconcile(OWNER)  # its own binding is always checked, whatever an earlier owner did
        assert seen == [("session-a", "session-a")] * 2
    mirror.reconcile(OTHER)
    assert len(seen) == 3, "nothing outlives the pass"


def test_a_failed_check_is_not_recorded_so_the_next_owner_retries_it(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    real = mirror._reconcile_binding
    attempts = []

    def flaky(owner, binding):
        attempts.append(owner["storedSessionId"])
        if len(attempts) == 1:
            raise OSError("busy")
        return real(owner, binding)

    monkeypatch.setattr(mirror, "_reconcile_binding", flaky)
    mod = type(mirror).reconcile.__globals__
    with mod["reconcile_pass"]():
        mirror.reconcile(OTHER)
        mirror.reconcile(OTHER)
        mirror.reconcile(OTHER)
    assert len(attempts) == 2, "retried once, then recorded"


def test_a_pass_still_withdraws_a_departed_ask_of_another_owner(tmp_path):
    import sqlite3
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    assert [r["state"] for r in store.requests(OWNER)] == ["open"]
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET status='ready', human_request=NULL")
    mod = type(mirror).reconcile.__globals__
    with mod["reconcile_pass"]():
        mirror.reconcile(OTHER)
        mirror.reconcile(OTHER)
    assert store.requests(OWNER) == [], "the older owner's request was withdrawn by the other owner's sweep"


def test_success_then_failed_own_check_revokes_marker(tmp_path, monkeypatch):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    real = mirror._reconcile_binding
    attempts = []

    def flaky(owner, binding):
        attempts.append(owner["storedSessionId"])
        if len(attempts) == 2:
            raise OSError("own-owner source unavailable")
        return real(owner, binding)

    monkeypatch.setattr(mirror, "_reconcile_binding", flaky)
    scope = type(mirror).reconcile.__globals__["reconcile_pass"]
    with scope():
        mirror.reconcile(OTHER)
        mirror.reconcile(OWNER)
        mirror.reconcile(OTHER)
        mirror.reconcile(OTHER)
    assert len(attempts) == 3, "failed mandatory recheck must revoke the earlier success"


def same_generation(first, second):
    """fixture() loads a new module each call; real passes share one generation."""
    result = type(first)(core, second.store, boards=second.boards, backend=second.backend,
                         session_exists=second.session_exists, tip=second.tip)
    result.remote.gateway = second.remote.gateway
    return result


def test_identical_bindings_in_different_stores_do_not_share_success(tmp_path):
    import sqlite3
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, store_a, path_a, hr = fixture(tmp_path / "a")
    first.reconcile(OWNER)
    second, store_b, path_b, hr = fixture(tmp_path / "b")
    second = same_generation(first, second)
    second._pass_source = getattr(first, '_pass_source', None)  # same synthetic adapter; isolate backing paths
    # Preserve the exact binding and request identity, not two unrelated requests.
    with sqlite3.connect(store_a.path) as source, sqlite3.connect(store_b.path) as target:
        source.backup(target)
    with sqlite3.connect(path_b) as c:
        c.execute("UPDATE tasks SET status='ready', human_request=NULL")
    scope = type(first).reconcile.__globals__["reconcile_pass"]
    with scope():
        first.reconcile(OTHER)
        second.reconcile(OTHER)
    assert store_a.requests(OWNER)
    assert store_b.requests(OWNER) == [], "a different store/source must perform its own withdrawal check"


def test_same_store_different_board_source_does_not_share_success(tmp_path):
    import sqlite3
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, store, path_a, hr = fixture(tmp_path / "a")
    first.reconcile(OWNER)
    second, unused_store, path_b, hr = fixture(tmp_path / "b")
    second = same_generation(first, second)
    second.store = store
    second._pass_source = getattr(first, '_pass_source', None)  # isolate board-source coordinates
    with sqlite3.connect(path_b) as c:
        c.execute("UPDATE tasks SET status='ready', human_request=NULL")
    scope = type(first).reconcile.__globals__["reconcile_pass"]
    with scope():
        first.reconcile(OTHER)
        second.reconcile(OTHER)
    assert store.requests(OWNER) == [], "same action store does not imply the same board source"


def test_default_mirrors_same_backing_source_share_across_instances(tmp_path, monkeypatch):
    first, store, path, hr = fixture(tmp_path)
    first.reconcile(OWNER)
    # Production constructs a new default-adapter Mirror for each chat.
    second = type(first)(core, store, boards=first.boards)
    third = type(first)(core, store, boards=first.boards)
    for mirror in (second, third):
        mirror.backend = first.backend
        mirror.session_exists = first.session_exists
        mirror.session_present = first.session_present
        mirror.tip = first.tip
        mirror.remote.gateway = first.remote.gateway
    seen = counting(second, monkeypatch)
    monkeypatch.setattr(third, '_reconcile_binding', second._reconcile_binding)
    scope = type(first).reconcile.__globals__["reconcile_pass"]
    with scope():
        second.reconcile(OTHER)
        third.reconcile(OTHER)
    assert len(seen) == 1


def test_binding_namespace_remains_part_of_the_pass_key(tmp_path):
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    with store._read() as c:
        binding = dict(c.execute('SELECT * FROM kanban_projections').fetchone())
    key = type(mirror).reconcile.__globals__['_binding_key']
    assert key(binding, 'same-source') != key(dict(binding, namespace='another-namespace'), 'same-source')


def test_nested_pass_exception_cleanup_and_thread_isolation(tmp_path, monkeypatch):
    import threading
    mirror, store, path, hr = fixture(tmp_path)
    mirror.reconcile(OWNER)
    seen = counting(mirror, monkeypatch)
    scope = type(mirror).reconcile.__globals__["reconcile_pass"]
    with pytest.raises(RuntimeError, match="abort"):
        with scope():
            mirror.reconcile(OTHER)
            with scope():
                mirror.reconcile(OTHER)
            assert len(seen) == 1
            errors = []
            def other_thread():
                try:
                    with scope():
                        mirror.reconcile(OTHER)
                except Exception as exc:
                    errors.append(exc)
            worker = threading.Thread(target=other_thread)
            worker.start()
            worker.join(5)
            assert not worker.is_alive()
            assert errors == []
            assert len(seen) == 2, "another thread has an independent pass"
            raise RuntimeError("abort")
    with scope():
        mirror.reconcile(OTHER)
    assert len(seen) == 3, "exception exit must clear the prior pass"
