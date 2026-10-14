"""spin-out agent half: tell a parent session which follow-ups were spun out of it (and where they went), so it
stops offering them. The desktop plugin records launches via POST /api/plugins/spin-out/spun; the next turn in the
parent gets a one-time `[spin-out]` note through pre_llm_call. Planning/REST live in core.py + dashboard/plugin_api.py.
"""
import importlib.util
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _core():
    name = "_spin_out_core"
    path = Path(__file__).resolve().parent / "core.py"
    mod = sys.modules.get(name)
    if mod is None or getattr(mod, "__file__", None) != str(path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
    return mod


def pre_llm_call(session_id="", **_):
    try:
        if not session_id:
            return None
        core = _core()
        items = core.take_undelivered(session_id)
        return {"context": core.spun_note(items)} if items else None
    except Exception as exc:
        log.debug("spin-out pre_llm_call: %s", exc)
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", pre_llm_call)
