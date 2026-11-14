"""spin-out agent half: tell a parent session which follow-ups were spun out of it (and where they went), so it
stops offering them. The desktop plugin records launches via POST /api/plugins/spin-out/spun; the next turn in the
parent gets a one-time `[spin-out]` note through pre_llm_call. A spun-out child's first turn links the parent's
open cards its brief names to the child chat (core.link_spun_cards), so the child's task list shows what it drives. Planning/REST live in core.py + dashboard/plugin_api.py.
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


def pre_llm_call(session_id="", user_message=None, is_first_turn=False, **_):
    try:
        if not session_id:
            return None
        core = _core()
        notes = []
        if is_first_turn and isinstance(user_message, str) and "Spun out of parent session" in user_message:
            # A spun-out child takes over the parent's open cards its brief names (kanban-abb8d6e8a7).
            try:
                rows = core.link_spun_cards(session_id, user_message)
                if rows:
                    log.info("spin-out: linked %s to %s", [(r["task_id"], r["ok"]) for r in rows], session_id)
                    notes.append(core.linked_note(rows))
            except Exception as exc:
                log.warning("spin-out card link failed for %s: %s", session_id, exc)
        items = core.take_undelivered(session_id)
        if items:
            notes.append(core.spun_note(items))
        return {"context": "\n\n".join(notes)} if notes else None
    except Exception as exc:
        log.debug("spin-out pre_llm_call: %s", exc)
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", pre_llm_call)
