"""Early-answer provider for core's blocked clarify (tools/clarify_early.py).

A session blocked in ``clarify`` asks the human; if the same human already decided in the For-you queue, the runtime
cannot admit that decision as a turn (the turn is active, blocked on the very question). This provider hands the
recorded-but-undelivered decisions for the session to the clarify as its tool result, then marks the operations
delivered with a receipt naming the clarify's tool_call_id, so no later turn repeats them.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("user-steps")
VIA = "for-you-queue"

try:  # core module; a plugin on an older core registers nothing (see __init__.register)
    from tools.clarify_early import EarlyAnswer
except ImportError:
    from dataclasses import dataclass

    @dataclass
    class EarlyAnswer:  # same shape, so tests and older cores still work
        result: dict
        commit: Callable[[], Any]


def _load_sibling(name: str):
    import importlib.util
    import sys
    key = f"_user_steps_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(name + ".py"))
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return sys.modules.setdefault(key, mod)


def describe(op: dict) -> dict:
    """The decision as the clarify's reader needs it: request, title, the chosen label or typed text, when, via."""
    intent = op["intent"]; request = intent["request"]; choice_id = op["decision"]["choiceId"]
    label = next((c["label"] for c in request.get("choices", []) if c["id"] == choice_id), choice_id)
    text = intent.get("revisionText")
    return {"requestId": op["requestId"], "title": request.get("title", ""), "choice": text or label,
            "decidedAt": op["recordedAt"], "via": VIA}


def make_provider(*, store: Optional[Callable[[], Any]] = None, delivery: Optional[Callable[[], Any]] = None,
                  db: Optional[Path] = None, owner_for: Optional[Callable[[str], dict]] = None):
    """``provider(session_id, tool_call_id) -> tools.clarify_early.EarlyAnswer | None``."""
    core = _load_sibling("core")
    cards = _load_sibling("cards")
    store = store or core.ActionStore
    delivery = delivery or cards.DecisionDelivery
    owner_for = owner_for or core.server_owner

    def provider(session_id: str, tool_call_id: str):
        if not session_id:
            return None
        owner = owner_for(session_id)
        st = store()
        mine = set(core.lineage(session_id, db))
        # Same session, or either side is a compression ancestor of the other (the stored id rotates on compression).
        ops = [op for op in st.undelivered(owner['gatewayId'], owner['profile'])
               if op['owner']['storedSessionId'] in mine
               or session_id in core.lineage(op['owner']['storedSessionId'], db)]
        if not ops:
            return None
        decisions = [describe(op) for op in ops]
        notice = "David answered in the For-you queue: " + "; ".join(
            f"{d['title']}: {d['choice']}" for d in decisions)

        def commit():
            receiver = delivery()
            for op in ops:
                try:
                    st.dispatch(op["owner"], op["operationId"], receiver, early_tool_call_id=tool_call_id)
                except Exception:
                    log.warning("Early clarify delivery failed for one decision; the retrier will still deliver it")

        return EarlyAnswer({"status": "answered_elsewhere", "decisions": decisions, "notice": notice}, commit)

    return provider
