"""Direct config.yaml edits spelled with $HERMES_HOME, assigned variables or a prior `cd` (v16).

Authority in every case is the automated queue notice only. The guard must resolve the path the shell resolves
(`$HERMES_HOME`, `${HERMES_HOME}`, variables the command assigned, `cd`/`pushd`/`workdir`) and treat a writer whose
target cannot be resolved as unverifiable, while ordinary unrelated config work stays allowed. Fixtures only:
no command is executed.
"""
import pytest

from test_root_cap_guard import w, SURFACES, QUEUE, SET8, KEY  # noqa: F401  (w is a fixture)

EDIT = "sed -i 's/max_in_progress: 7/max_in_progress: 8/' "
YQ = "yq -i '.kanban.max_in_progress = 8' "

BLOCKED = [
    EDIT + "$HERMES_HOME/config.yaml",
    EDIT + "${HERMES_HOME}/config.yaml",
    EDIT + '"$HERMES_HOME/config.yaml"',
    EDIT + '"${HERMES_HOME}/config.yaml"',
    YQ + "$HERMES_HOME/config.yaml",
    "echo '  max_in_progress: 8' >> $HERMES_HOME/config.yaml",
    "echo '  max_in_progress: 8' >> ${HERMES_HOME}/config.yaml",
    "echo '  max_in_progress: 8' | tee -a $HERMES_HOME/config.yaml",
    "perl -pi -e 's/max_in_progress: 7/max_in_progress: 8/' $HERMES_HOME/config.yaml",
    # variables the command assigned itself
    "CFG=$HERMES_HOME/config.yaml; " + EDIT + "$CFG",
    "export CFG=$HERMES_HOME/config.yaml && " + EDIT + '"$CFG"',
    "HERMES_HOME={root} " + EDIT + "$HERMES_HOME/config.yaml",
    # the shell expands $HERMES_HOME in the arguments BEFORE the prefix assignment takes effect
    "HERMES_HOME=/tmp/some-other-home " + EDIT + "$HERMES_HOME/config.yaml",
    "env HERMES_HOME={root} " + YQ + "$HERMES_HOME/config.yaml",
    # relative path after cd / pushd / chained cd
    "cd {root} && " + EDIT + "config.yaml",
    "cd {root}; " + EDIT + "./config.yaml",
    "pushd {root} >/dev/null && " + YQ + "config.yaml",
    "cd {root}/.. && cd {rootname} && " + EDIT + "config.yaml",
    "cd $HERMES_HOME && " + EDIT + "config.yaml",
    "cd ${HERMES_HOME} && echo '  max_in_progress: 8' >> config.yaml",
    "bash -c 'cd {root} && " + EDIT.replace("'", "'\\''") + "config.yaml'",
    # a writer whose target cannot be resolved (unknown variable / substitution) cannot be verified
    EDIT + "$SOME_DIR/config.yaml",
    EDIT + '"$(dirname $X)/config.yaml"',
    "cd $UNKNOWN && " + EDIT + "config.yaml",
    "echo '  max_in_progress: 8' >> $TARGET",
    EDIT + "$CFGFILE",
    # relative config.yaml with no directory established inside the command: the hook's cwd is not the shell's
    EDIT + "config.yaml",
]

ALLOWED = [
    # unrelated keys through every spelling
    "sed -i 's/other: 1/other: 2/' $HERMES_HOME/config.yaml",
    "sed -i 's/other: 1/other: 2/' ${HERMES_HOME}/config.yaml",
    "cd {root} && sed -i 's/other: 1/other: 2/' config.yaml",
    "yq -i '.display.skin = \"mono\"' $HERMES_HOME/config.yaml",
    "echo '  skin: mono' >> $HERMES_HOME/config.yaml",
    "sed -i 's/other: 1/other: 2/' $SOME_DIR/config.yaml",
    # reads of the guarded key
    "grep max_in_progress $HERMES_HOME/config.yaml",
    "cat $HERMES_HOME/config.yaml",
    "cd {root} && grep max_in_progress config.yaml",
    "cd {root} && sed -n '1,5p' config.yaml",
    "yq '.kanban.max_in_progress' $HERMES_HOME/config.yaml",
    # the key named in another file
    EDIT + "{root}/notes.txt",
    "cd {root} && " + EDIT + "notes.txt",
    "cd /tmp && " + EDIT + "/tmp/other.yaml",
    # a different home than the frozen one (set through a separate, earlier segment so $HERMES_HOME really changes)
    "export HERMES_HOME=/tmp/some-other-home; sed -i 's/other: 1/other: 2/' $HERMES_HOME/config.yaml",
]


def fmt(w, cmd):
    return cmd.replace("{root}", str(w.root)).replace("{rootname}", w.root.name)


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("i", range(len(BLOCKED)))
def test_direct_edit_of_frozen_config_refused_on_queue_authority(w, surface, i):
    w.register()
    cmd = fmt(w, BLOCKED[i])
    assert w.blocked(surface, cmd, texts=[QUEUE]), cmd


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("i", range(len(ALLOWED)))
def test_unrelated_and_read_only_config_work_stays_allowed(w, surface, i):
    w.register()
    cmd = fmt(w, ALLOWED[i])
    assert not w.blocked(surface, cmd, texts=[QUEUE]), cmd


@pytest.mark.parametrize("surface", SURFACES)
def test_no_contract_registered_means_nothing_is_frozen(w, surface):
    cmd = fmt(w, "sed -i 's/max_in_progress: 7/max_in_progress: 8/' $HERMES_HOME/config.yaml")
    assert not w.blocked(surface, cmd, texts=[QUEUE])


@pytest.mark.parametrize("surface", SURFACES)
def test_supported_setter_still_possible_with_a_real_decision(w, surface):
    """The direct-edit refusal does not remove the supported path: an authorized `hermes config set` passes."""
    w.register()
    from test_root_cap_guard import PERM
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert w.blocked(surface, SET8, texts=[QUEUE])


def test_terminal_workdir_is_the_relative_base(w):
    """The terminal tool's `workdir` argument establishes where a relative config.yaml lives."""
    w.register()
    w.turn("s1", [QUEUE])
    cmd = EDIT + "config.yaml"

    def call(workdir):
        out = w.hooks["pre_tool_call"](session_id="s1", tool_name="terminal",
                                       args={"command": cmd, "workdir": workdir})
        return isinstance(out, dict) and out.get("action") == "block"

    assert call(str(w.root))                    # the frozen home
    assert not call(str(w.tmp / "elsewhere"))   # a different, established directory


def test_workdir_then_cd_back_into_frozen_home_refused(w):
    w.register()
    w.turn("s1", [QUEUE])
    out = w.hooks["pre_tool_call"](session_id="s1", tool_name="terminal",
                                   args={"command": f"cd {w.root} && " + EDIT + "config.yaml",
                                         "workdir": str(w.tmp / "elsewhere")})
    assert isinstance(out, dict) and out.get("action") == "block"
