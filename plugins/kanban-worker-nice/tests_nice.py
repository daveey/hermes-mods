import importlib.util
import os
import subprocess
import sys
from pathlib import Path

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


def test_children_inherit_real_process():
    """Real setpriority in a subprocess; a grandchild shell reports the inherited nice."""
    code = (
        "import importlib.util,subprocess,sys;"
        f"sp=importlib.util.spec_from_file_location('k',{str(Path(__file__).with_name('__init__.py'))!r});"
        "m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);m.apply();"
        "print(subprocess.check_output(['sh','-c','ps -o nice= -p $$']).decode().strip())"
    )
    env = dict(os.environ, HERMES_KANBAN_TASK="t_test")
    env.pop(kwn.ENV_VAR, None)
    out = subprocess.check_output([sys.executable, "-c", code], env=env).decode().strip()
    assert out == "10"
