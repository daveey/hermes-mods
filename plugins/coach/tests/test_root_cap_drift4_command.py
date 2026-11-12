"""Fourth real cap drift (reported by the rollout owner, 23:50Z): a compound terminal command written in response to an
automated queue notice. The exact command text is the owner's quoted record; the native row ids were not reachable
from this worker, so only the command shape and the queue-notice-only authority are reproduced. Fixture homes only."""
import pytest

from test_root_cap_guard import w, KEY, QUEUE, SURFACES

DRIFT4 = ("uptime | sed 's/.*load/load/'; hermes config set kanban.max_in_progress 8 2>&1|tail -1; sleep 40; "
          "sqlite3 -readonly ~/.hermes/kanban.db \"select status from tasks where id='t_01a1cbd8aa338b40'\"")
READONLY = "sqlite3 -readonly ~/.hermes/kanban.db \"select status from tasks where id='t_01a1cbd8aa338b40'\"; uptime | sed 's/.*load/load/'"


@pytest.mark.parametrize("surface", SURFACES)
def test_compound_command_after_queue_notice_is_refused(w, surface):
    w.register()
    assert w.blocked(surface, DRIFT4, texts=[QUEUE])


@pytest.mark.parametrize("surface", SURFACES)
def test_same_compound_without_the_cap_write_is_allowed(w, surface):
    w.register()
    assert not w.blocked(surface, READONLY, texts=[QUEUE])
    assert not w.blocked(surface, DRIFT4.replace("kanban.max_in_progress 8", "display.skin default"), texts=[QUEUE])


@pytest.mark.parametrize("surface", SURFACES)
def test_registered_freeze_is_not_assumed_by_the_guard_for_other_keys(w, surface):
    w.register()
    assert not w.blocked(surface, f"hermes config set {KEY.replace('max_in_progress', 'unrelated')} 8", texts=[QUEUE])
