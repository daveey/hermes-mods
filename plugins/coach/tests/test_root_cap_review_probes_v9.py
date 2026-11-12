"""Independent v9 review: later native user restrictions must constrain the surviving authorization.
Run with exact-head plugins/coach/tests on PYTHONPATH; no config command is executed.
"""
import json
import pytest
from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP_OWNER, PERM, TEMP

BASE = f"I authorize changing root {KEY} from 7 to 8."
TERMS = (f"Owner card {TEMP_OWNER}, run 1, this session. "
         f"Restore root {KEY} to 7 on owner completion.")

@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("restriction", [
    f"The root {KEY} change from 7 to 8 is temporary, not permanent.",
    "Make that temporary, not permanent.",
])
def test_later_native_restriction_requires_provenance(w, surface, restriction):
    w.register()
    assert w.blocked(surface, SET8, texts=[BASE, restriction]), \
        "later native restriction was ignored; write admitted without temporary provenance"

@pytest.mark.parametrize("surface", SURFACES)
def test_later_native_terms_persist_temporary(w, surface):
    w.register()
    restriction = f"The root {KEY} change from 7 to 8 is temporary. {TERMS}"
    assert not w.blocked(surface, SET8, texts=[BASE, restriction])
    rows = json.loads((w.shared / "root_cap_exceptions.json").read_text())
    assert rows[-1]["kind"] == "temporary", "later own owner/restore linkage silently discarded"
    assert rows[-1]["owner_ref"]["card"] == TEMP_OWNER
    assert rows[-1]["restore_value"] == 7

@pytest.mark.parametrize("surface", SURFACES)
def test_controls(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert not w.blocked(surface, SET8, texts=[TEMP])
    assert not w.blocked(surface, "hermes config set display.skin default", texts=[BASE, "Make that temporary."])
    assert not w.blocked(surface, SET8, texts=[BASE, 'The assistant wrote: "Make that temporary."'])
    assert w.blocked(surface, SET8, texts=[BASE, "Cancel that."])
