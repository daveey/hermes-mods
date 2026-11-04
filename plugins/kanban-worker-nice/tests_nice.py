import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("kwn", Path(__file__).with_name("__init__.py"))
kwn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kwn)


def _fake(start):
    state = {"n": start}
    return state, (lambda: state["n"]), (lambda n: state.__setitem__("n", n))


def test_non_worker_untouched():
    st, g, s = _fake(0)
    assert kwn.apply({}, g, s) is None and st["n"] == 0


def test_worker_niced_default():
    st, g, s = _fake(0)
    assert kwn.apply({"HERMES_KANBAN_TASK": "t_1"}, g, s) == 10 and st["n"] == 10


def test_env_override_and_disable():
    st, g, s = _fake(0)
    assert kwn.apply({"HERMES_KANBAN_TASK": "t", kwn.ENV_VAR: "15"}, g, s) == 15
    st, g, s = _fake(0)
    assert kwn.apply({"HERMES_KANBAN_TASK": "t", kwn.ENV_VAR: "0"}, g, s) is None and st["n"] == 0


def test_never_lowers():
    st, g, s = _fake(19)
    assert kwn.apply({"HERMES_KANBAN_TASK": "t"}, g, s) is None and st["n"] == 19


@pytest.mark.parametrize("initial_floor", [0, 15, 19])
def test_children_inherit_real_process(initial_floor):
    """Worker and grandchild preserve inherited niceness above the default target."""
    code = (
        "import importlib.util,json,os,subprocess;"
        # Change only the subprocess, never the pytest runner, and never require
        # privilege to undo niceness inherited from a background suite launcher.
        f"os.setpriority(os.PRIO_PROCESS,0,max(os.getpriority(os.PRIO_PROCESS,0),{initial_floor}));"
        "before=os.getpriority(os.PRIO_PROCESS,0);"
        f"sp=importlib.util.spec_from_file_location('k',{str(Path(__file__).with_name('__init__.py'))!r});"
        "m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);m.apply();"
        "after=os.getpriority(os.PRIO_PROCESS,0);"
        "child=int(subprocess.check_output(['sh','-c','ps -o nice= -p $$']));"
        "print(json.dumps([before,after,child]))"
    )
    env = dict(os.environ, HERMES_KANBAN_TASK="t_test")
    env.pop(kwn.ENV_VAR, None)
    before, after, child = json.loads(
        subprocess.check_output([sys.executable, "-c", code], env=env, timeout=15)
    )
    assert before >= initial_floor
    expected = max(before, 10)
    assert after == expected
    assert child == expected
