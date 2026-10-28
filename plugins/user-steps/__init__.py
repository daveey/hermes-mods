"""Canonical user decisions, with passive transcript receipts.

The action_request tool publishes explicit versioned scope. transform_llm_output
only proposes incomplete requests; it cannot authorize work or resolve decisions.
pre_llm_call projects confirmed decision receipts into a normal turn. It never
acknowledges pending delivery or starts a concurrent agent turn.
Legacy rolling cards are not automatically created, unblocked or archived.
Skipped: Kanban workers, subagents, cron/webhook. Off switch: HERMES_USER_STEPS_OFF=1.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

try:
    from . import cards as cards_mod, core, chat
except ImportError:  # pytest/spec loading imports the hyphenated plugin dir without a parent package
    import importlib.util as _ilu
    import sys as _sys

    def _sib(name: str):
        key = "user_steps_" + name
        if key in _sys.modules:
            return _sys.modules[key]
        spec = _ilu.spec_from_file_location(key, Path(__file__).with_name(name + ".py"))
        mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return _sys.modules.setdefault(key, mod)

    core, cards_mod, chat = _sib("core"), _sib("cards"), _sib("chat")

log = logging.getLogger("user-steps")
SKIP_PLATFORMS = {"cron", "kanban", "webhook", "subagent"}
_USER_MSG: dict[str, str] = {}
_BACKFILLED = False


def _eligible(session_id: str, platform: str = "", parent_session_id: str = "") -> bool:
    if not session_id or parent_session_id:
        return False
    if os.environ.get("HERMES_KANBAN_TASK") or os.environ.get("HERMES_USER_STEPS_OFF"):
        return False
    return (platform or "").lower() not in SKIP_PLATFORMS


def _profile() -> str:
    return os.environ.get("HERMES_SESSION_PROFILE") or os.environ.get("HERMES_PROFILE") or "default"


def _model_cfg() -> tuple[str | None, str | None]:
    prov, model = os.environ.get("USER_STEPS_PROVIDER"), os.environ.get("USER_STEPS_MODEL")
    if prov or model:
        return prov, model
    try:  # an explicit auxiliary.user_steps block wins; otherwise a fast Claude model (this runs on the reply path)
        from hermes_cli.config import load_config_readonly  # type: ignore

        cfg = (load_config_readonly().get("auxiliary") or {}).get("user_steps") or {}
        if cfg.get("model") or (cfg.get("provider") and cfg.get("provider") != "auto"):
            return None, None
    except Exception:
        pass
    return "anthropic", "claude-haiku-5-5"


def aux_llm(system: str, user: str) -> str:
    from agent.auxiliary_client import call_llm  # type: ignore

    prov, model = _model_cfg()
    kw: dict[str, Any] = {}
    if prov:
        kw["provider"] = prov
    if model:
        kw["model"] = model
    resp = call_llm(task="user_steps", messages=[{"role": "system", "content": system},
                                                  {"role": "user", "content": user}],
                    max_tokens=900, temperature=None, timeout=25, reasoning_config={"enabled": False}, **kw)
    return (getattr(resp.choices[0].message, "content", None) or "").strip()


def _ledger():
    return core.Ledger()


def _trace(**kw: Any) -> None:
    """One JSON line per decision under <home>/cache/user-steps/trace.jsonl (kept to ~2000 lines)."""
    try:
        import json
        import time

        p = core.home() / "cache" / "user-steps" / "trace.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > 1_000_000:
            p.write_text("\n".join(p.read_text().splitlines()[-2000:]) + "\n")
        with p.open("a") as f:
            f.write(json.dumps({"t": round(time.time(), 3), "pid": os.getpid(), **kw}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _framed(session_id: str) -> bool:
    """Only the Desktop renders `::user-step`; CLI/TUI/messaging chats keep plain text."""
    forced = os.environ.get("HERMES_USER_STEPS_FRAME")
    if forced is not None:
        return forced == "1"
    return core.session_source(session_id) == "desktop"


# ---- hooks ---------------------------------------------------------------------------------------------

def pre_llm_call(session_id: str = "", user_message: str = "", platform: str = "", parent_session_id: str = "",
                 **_: Any) -> dict | None:
    if not _eligible(session_id, platform, parent_session_id):
        return None
    try:
        _USER_MSG[session_id] = user_message if isinstance(user_message, str) else ""
        own = core.server_owner(session_id)
        st = core.ActionStore()
        global _BACKFILLED
        if not _BACKFILLED:
            _BACKFILLED = True
            try:
                st.backfill_legacy(_ledger(), gateway=own['gatewayId'])
            except Exception:
                log.warning("user-steps legacy backfill deferred")
        chat.reconcile(core, st, own, aux_llm)
        for step in _ledger().read(session_id).get('steps', []):
            if step.get('session') == session_id and step.get('status') == 'open':
                st.migrate_step(own, step)
        # A passive inbox is NOT a delivery receiver. Never acknowledge pending
        # card/run work from this hook, nor wake a second writer inside a live turn.
        # The owner API retries the persisted operation using the same key.
        requests = st.requests(own)
        receipts = cards_mod.DecisionInbox().context(own, requests)
        pending = [{k: v for k, v in r.items() if k != 'resolution'}
                   for r in requests if r['state'] in ('open', 'needs_context') and not r.get('resolution')]
        import json
        note = ('[Canonical requests awaiting a user decision: this list grants NO execution '
                'permission for the requests listed here. Recorded decisions are omitted: '
                'their authority comes from the admitted user decision or confirmed receipt, '
                'not from this list. Legacy steps and extracted text cannot grant permission.]\n'
                + json.dumps(pending)) if pending else ''
        incomplete = [r for r in pending if r['kind'] == 'approval' and not r['scope']['complete']]
        if incomplete:
            # Missing plans are agent work: the user has no button for them.
            note += ('\n[Agent work: these approvals lack a complete plan, so the user cannot decide them. '
                     'If still needed, publish action_request with the same requestId and expectedRevision, '
                     'the FULL exact plan, scope, consequences and approve/change/decline choices. Do not ask '
                     'the user to request a plan.] ' + json.dumps([{'requestId': r['requestId'],
                                                                     'expectedRevision': r['revision'],
                                                                     'title': r['title']} for r in incomplete]))
        context = '\n'.join(x for x in (note, receipts) if x)
        return {'context': context} if context else None
    except Exception:
        log.exception("user-steps pre_llm_call failed")
        return None


def transform_llm_output(response_text: str = "", session_id: str = "", platform: str = "", **_: Any):
    if not isinstance(response_text, str) or not response_text.strip():
        return None
    if not _eligible(session_id, platform):
        return None
    try:
        try:
            action_owner = core.server_owner(session_id)
        except core.ActionError:
            action_owner = None  # no authenticated owner: ledger-only, no canonical request is minted
        if action_owner:
            chat.reconcile(core, core.ActionStore(), action_owner, aux_llm)
        out = core.process_reply(
            response_text, session_id, user_msg=_USER_MSG.pop(session_id, ""), llm=aux_llm, ledger=_ledger(),
            cards=None, framed=_framed(session_id),
            action_store=core.ActionStore() if action_owner else None, action_owner=action_owner,
            session_ref=f"@session:{_profile()}/{session_id}")
        if out.get("error"):
            log.warning("user-steps extractor fell back to regex: %s", out["error"])
        if out.get("dropped"):
            log.info("user-steps dropped %d terminal-doable step(s): %s", len(out["dropped"]), out["dropped"])
        _trace(hook="transform", session=session_id, platform=platform, source=out.get("source"),
               new=[s["id"] for s in out.get("steps", [])], resolved=out.get("resolved"),
               framed=out.get("text") is not None, error=out.get("error"),
               dropped_terminal=out.get("dropped") or None)
        return out.get("text")
    except Exception as exc:
        _trace(hook="transform", session=session_id, platform=platform, exception=repr(exc)[:300])
        log.exception("user-steps transform failed")
        return None  # fail open: the reply is shown unchanged


def publication_buffer(session_id='', platform='', final_response='', attempt=0,
                       hold_budget=2, **_: Any):
    """Return missing plan work to the agent, never to a user-facing button."""
    if not _eligible(session_id, platform) or not core._worth_asking(final_response):
        return None
    asks = core.extract(final_response, [], '', aux_llm).get('steps', [])
    approvals = [s for s in asks if s['kind'] == 'approve']
    if not approvals:
        return None
    owner = core.server_owner(session_id)
    requests = core.ActionStore().requests(owner)
    complete = [r for r in requests if r['kind'] == 'approval' and r['state'] == 'open'
                and r['scope']['complete'] and not r.get('resolution')
                and {'approve', 'revise', 'decline'} <= {c['effect'] for c in r['choices']}]
    if all(any(core._norm(s['text']) == core._norm(r['title']) for r in complete) for s in approvals):
        return None
    if attempt >= hold_budget:
        return {'replace': 'The approval request is not ready: the agent has not provided a complete exact plan. No approval is requested and no permission has been granted.'}
    return {'hold': True, 'nudge': 'Complete the missing agent work now: publish action_request with the FULL EXACT inline plan, scope, consequences, limits, exclusions and approve/change/decline choices. Use the same question as its title. Do not ask the user to request a plan, and do not invent approval. If this is only a clarification, publish kind choice/input with concrete answer labels instead. Then present the decision.'}


publication_buffer.needs_buffer = True
publication_buffer.accepts_hold_budget = True


def _require_publication_contract() -> None:
    try:
        from agent.pre_final_gate import PRE_FINAL_GATE_CAPABILITIES
    except ImportError as exc:
        raise RuntimeError("user-steps requires post-transform buffered publication; update core first") from exc
    contract = PRE_FINAL_GATE_CAPABILITIES
    if not isinstance(contract, dict) or any(
        contract.get(key) is not True for key in ("needs_buffer", "post_transform_release")
    ):
        raise RuntimeError("user-steps requires post-transform buffered publication; update core first")


def publish_action_request(args: dict, session_id: str = '', **_) -> str:
    """Agent producer: explicit scope only. Extraction NEVER calls this complete-plan path.

    session_id is injected by tool dispatch; no owner/session payload is accepted.
    Existing IDs require expectedRevision and publish a new immutable scope revision.
    """
    import json
    import uuid
    try:
        core._object(args, ('request',), ('requestId', 'expectedRevision', 'decisionCard'))
        owner = core.server_owner(session_id)
        payload = dict(args['request'])
        if payload.get('kind') == 'approval' and not (payload.get('scope') or {}).get('complete'):
            raise core.ActionError('Complete the full exact inline plan, scope and consequences before publishing approval. This is agent work; do not ask the user to request a plan.')
        if payload.get('kind') == 'approval' and not {'approve', 'revise', 'decline'} <= {
                c.get('effect') for c in payload.get('choices', [])}:
            raise core.ActionError('Provide approve, revise and decline choices with their exact outcomes.')
        if any(key in payload for key in ('owner', 'requestId', 'revision', 'resolution')):
            raise core.ActionError('Producer identity is server-owned')
        rid = args.get('requestId') or str(uuid.uuid4())
        st = core.ActionStore()
        payload.update(owner=owner, requestId=rid, revision=1)
        adapter = cards_mod.QualifiedCardDelivery()
        # Aliases alone never authorize adopting a card. Only producer-created
        # cards survive this path; replacement plans preserve the original binding.
        refs = [ref for ref in payload.get('aliases', []) if ref.get('type') == 'kanban']
        if args.get('requestId'):
            if 'expectedRevision' not in args: raise core.ActionError('expectedRevision required')
            if 'decisionCard' in args: raise core.ActionError('Cannot add a card to a replacement')
            old_refs = [ref for ref in st.get(owner, rid)['aliases'] if ref['type'] == 'kanban']
            if refs != old_refs: raise core.ActionError('Replacement must retain exact producer card aliases')
            out = st.revise(owner, rid, args['expectedRevision'], payload, bind=adapter.bind_request)
        else:
            if 'expectedRevision' in args: raise core.ActionError('New requests have no expectedRevision')
            if refs: raise core.ActionError('Card aliases must be created by decisionCard, not supplied')
            payload = core.validate_action(payload)
            if 'decisionCard' in args:
                core._object(args['decisionCard'], ('board',))
                ref = adapter.prepare(payload, args['decisionCard']['board'])
                payload['aliases'].append(ref)
            out = st.create(owner, payload, bind=adapter.bind_request)
        return json.dumps({'request': out})
    except (ValueError, TypeError, RuntimeError) as exc:
        return json.dumps({'error': str(exc), 'status': getattr(exc, 'status', 422)})


PRODUCER_SCHEMA = {
    'name': 'action_request',
    'description': 'Publish a complete, inline ActionRequest v2 for the current session. Include exact scope, limits, exclusions, outcomes and explicit choices. No execution occurs. To replace scope, reuse requestId with expectedRevision. Never claim completeness for an inferred or missing plan.',
    'parameters': {'type': 'object', 'additionalProperties': False,
                   'properties': {'request': {'type': 'object', 'description': 'ActionRequest v2 fields except server-owned owner, requestId, revision and resolution. Required: schemaVersion=2, kind, title, scope {markdown,complete,sourceRefs}, limits, exclusions, outcomes {revise,approve?,decline?}, choices [{id,label,effect}], qualified aliases, state (open or needs_context).'},
                                  'requestId': {'type': 'string'}, 'expectedRevision': {'type': 'integer'},
                                  'decisionCard': {'type': 'object', 'additionalProperties': False,
                                                   'properties': {'board': {'type': 'string'}}, 'required': ['board'],
                                                   'description': 'Optional new dedicated blocked decision metadata card. Never an existing card ID.'}},
                   'required': ['request']}}


def register(ctx) -> None:
    _require_publication_contract()
    ctx.register_hook("pre_final_gate", publication_buffer)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("transform_llm_output", transform_llm_output)
    ctx.register_tool(name='action_request', toolset='user_steps', schema=PRODUCER_SCHEMA,
                      handler=publish_action_request, description=PRODUCER_SCHEMA['description'])
