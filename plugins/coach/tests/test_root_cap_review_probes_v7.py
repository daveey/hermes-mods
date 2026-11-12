"""Independent contract probes: explicit bounded/restore intent is not permanent authority.
Fixture callbacks only: no config command is executed.
Run with the exact-head plugins/coach/tests on PYTHONPATH.
"""
import json
import pytest
from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP_OWNER, PERM, TEMP

BASE = f"I authorize changing root {KEY} from 7 to 8."

@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("restriction", [
    "This is non-permanent.",
    "This is non permanent.",
    "This change is only for 10 minutes.",
    f"Restore root {KEY} to 7 on owner completion.",
])
def test_bounded_override_without_complete_terms_refuses(w, surface, restriction):
    w.register()
    assert w.blocked(surface, SET8, texts=[BASE + " " + restriction]), restriction

@pytest.mark.parametrize("surface", SURFACES)
def test_restore_linked_override_is_recorded_as_temporary(w, surface):
    w.register()
    text = (BASE + f" Owner card {TEMP_OWNER}, run 1, this session. "
            f"Restore root {KEY} to 7 on owner completion.")
    assert not w.blocked(surface, SET8, texts=[text])
    rows = json.loads((w.shared / "root_cap_exceptions.json").read_text())
    assert rows[-1]["kind"] == "temporary", rows[-1]["kind"]
    assert rows[-1]["restore_value"] == 7
    assert rows[-1]["owner_ref"]["card"] == TEMP_OWNER

@pytest.mark.parametrize("surface", SURFACES)
def test_existing_positive_controls(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert not w.blocked(surface, SET8, texts=[TEMP])
    assert not w.blocked(surface, "hermes config set display.skin default", texts=[BASE + " This is non-permanent."])
