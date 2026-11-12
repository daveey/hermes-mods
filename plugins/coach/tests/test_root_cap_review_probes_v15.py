"""Reviewer probe (v15, round 16): direct config.yaml edits whose PATH is spelled with $HERMES_HOME or a prior `cd`
(the repo-documented spellings). QUEUE-notice authority only; fixtures only, nothing is executed."""
import pytest
from test_root_cap_guard import w, SURFACES, QUEUE

CASES = [
    "sed -i 's/max_in_progress: 7/max_in_progress: 8/' $HERMES_HOME/config.yaml",
    "sed -i 's/max_in_progress: 7/max_in_progress: 8/' ${HERMES_HOME}/config.yaml",
    "sed -i 's/max_in_progress: 7/max_in_progress: 8/' \"$HERMES_HOME/config.yaml\"",
    "echo '  max_in_progress: 8' >> $HERMES_HOME/config.yaml",
    "yq -i '.kanban.max_in_progress = 8' $HERMES_HOME/config.yaml",
    "cd {root} && sed -i 's/max_in_progress: 7/max_in_progress: 8/' config.yaml",
    "cd {root} && yq -i '.kanban.max_in_progress = 8' config.yaml",
    "pushd {root} >/dev/null; sed -i 's/max_in_progress: 7/max_in_progress: 8/' config.yaml",
    "python3 -c \"import os,re;p=os.path.join(os.environ['HERMES_HOME'],'config.yaml');t=open(p).read();open(p,'w').write(t.replace('max_in_progress: 7','max_in_progress: 8'))\"",
]
CONTROL = "sed -i 's/other: 1/other: 2/' $HERMES_HOME/config.yaml"


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("i", range(len(CASES)))
def test_direct_edit_blocked(w, surface, i):
    w.register()
    cmd = CASES[i].replace("{root}", str(w.root))
    assert w.blocked(surface, cmd, texts=[QUEUE]), cmd


@pytest.mark.parametrize("surface", SURFACES)
def test_control_other_key_allowed(w, surface):
    w.register()
    assert not w.blocked(surface, CONTROL, texts=[QUEUE])
