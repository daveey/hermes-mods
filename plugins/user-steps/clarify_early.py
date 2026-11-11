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
    class EarlyAnswer:  # same shape, so tests and older cores still work
        # Sibling loaders publish modules only after exec_module, avoiding half-loaded
        # modules in other threads. A dataclass with postponed annotations requires
        # sys.modules during class creation, so keep this fallback a plain container.
        def __init__(self, result: dict, commit: Callable[[], Any]):
            self.result = result
            self.commit = commit


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
    """The decision as the clarify's reader needs it: request, title, the chosen label or typed text, when, via, and
    the exact bound identity (operation, revision, scope digest, frozen effect) so the owner can acknowledge it."""
    intent = op["intent"]; request = intent["request"]; choice_id = op["decision"]["choiceId"]
    label = next((c["label"] for c in request.get("choices", []) if c["id"] == choice_id), choice_id)
    text = intent.get("revisionText")
    out = {"requestId": op["requestId"], "title": request.get("title", ""), "choice": text or label,
           "decidedAt": op["recordedAt"], "via": VIA}
    if op.get("binding"):
        out.update(operationId=op["operationId"], revision=op["revision"], scopeDigest=op["binding"]["scopeDigest"],
                   effect=intent["effect"], executionAuthorized=intent.get("executionAuthorized") is True,
                   permissionGrants=[])
    return out


RECEIPT_HINT = ('After validating each decision, acknowledge it with action_request {"receipt": {"operationId", '
                '"phase": "acknowledged", "revision", "scopeDigest"}}; after acting, record phase "applied" with '
                'evidence refs. "not_applied" (with a reason) is only for declining before reserving; a reserved '
                'operation stays uncertain, never not_applied. Receipts grant nothing.')


def make_provider(*, store: Optional[Callable[[], Any]] = None, delivery: Optional[Callable[[], Any]] = None,
                  db: Optional[Path] = None, owner_for: Optional[Callable[[str], dict]] = None):
    """``provider(session_id, tool_call_id, question=descriptor) -> tools.clarify_early.EarlyAnswer | None``.

    On first display the clarify is bound (``ActionStore.bind_clarify``) to the single undecided-or-pending request
    revision of a related session whose question key (normalized text + ORDERED labels) matches each displayed
    question; that match is frozen. Only when every displayed question has such a match and each has a recorded,
    undelivered decision with the same revision and scope digest is the clarify answered. Session lineage is
    required but never sufficient. Without a descriptor (an older core) nothing is returned: the decisions stay in
    the outbox for normal delivery."""
    core = _load_sibling("core")
    cards = _load_sibling("cards")
    store = store or core.ActionStore
    delivery = delivery or cards.DecisionDelivery
    owner_for = owner_for or core.server_owner

    def provider(session_id: str, tool_call_id: str, question: Optional[dict] = None):
        if not session_id or not isinstance(question, dict) or not tool_call_id:
            return None
        if question.get("toolCallId") != tool_call_id or not isinstance(question.get("digest"), str):
            return None
        owner = owner_for(session_id)
        st = store()
        mine = set(core.lineage(session_id, db))

        def related(stored):  # same session, or either side is a compression ancestor of the other
            return stored in mine or session_id in core.lineage(stored, db)
        try:
            bound = st.bind_clarify(owner, question, related)  # render-time binding, frozen on first display
        except Exception:
            log.warning("Clarify binding refused; nothing is answered early", exc_info=True)
            return None
        wanted = {(m["requestId"], m["revision"], m["scopeDigest"]) for m in bound["matches"]}
        if any(m["requestId"] is None for m in bound["matches"]):
            return None  # every displayed question must be bound to exactly one request, or none is answered
        ops = []
        for op in st.undelivered(owner['gatewayId'], owner['profile']):
            b = op.get("binding")
            if not b or not related(op['owner']['storedSessionId']):
                continue
            if (op["requestId"], op["revision"], b.get("scopeDigest")) not in wanted:
                continue  # unrelated (or pre-v1, unbound) decision of the same session: never ends this clarify
            if b.get("scopeDigest") != core.scope_digest(op["intent"]["request"]):
                continue
            try:
                current = st.get(op["owner"], op["requestId"])
            except Exception:
                continue
            if current.get("revision") != op["revision"]:
                continue  # revised since: the stale decision cannot answer the new question
            ops.append(op)
        if {(o["requestId"], o["revision"]) for o in ops} != {(r, v) for r, v, _ in wanted}:
            return None  # a partial answer would silently drop a displayed question
        if not ops:
            return None
        decisions = [describe(op) for op in ops]
        notice = "David answered in the For-you queue: " + "; ".join(
            f"{d['title']}: {d['choice']}" for d in decisions) + ". " + RECEIPT_HINT
        binding = {"toolCallId": tool_call_id, "questionDigest": question["digest"]}

        def commit():
            receiver = delivery()
            for op in ops:
                try:
                    st.dispatch(op["owner"], op["operationId"], receiver, early_tool_call_id=tool_call_id,
                                early_binding=dict(binding, questionKey=op["binding"]["questionKey"]))
                except Exception:
                    log.warning("Early clarify delivery failed for one decision; the retrier will still deliver it")

        return EarlyAnswer({"status": "answered_elsewhere", "decisions": decisions, "notice": notice,
                            "binding": binding}, commit)

    provider.accepts_question = True
    return provider
