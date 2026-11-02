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
# Request ids the producer tool published or revised per stored session during the turn in flight. Server-side
# provenance: only these may absorb the reply's restatement of their title or menu (core.same_open_ask).
# Consumed by the turn's final reply transform; the gate only reads it.
_TURN_PUBLISHED: dict[str, set] = {}


def _published(owner: dict, request: dict) -> dict:
    sid = (owner or {}).get('storedSessionId')
    if sid and isinstance(request, dict) and request.get('requestId'):
        # Bounded: sessions whose turn never reaches the reply transform (ineligible/background) are evicted
        # oldest-first instead of accumulating for the life of the process.
        while len(_TURN_PUBLISHED) >= 256 and sid not in _TURN_PUBLISHED:
            _TURN_PUBLISHED.pop(next(iter(_TURN_PUBLISHED)))
        _TURN_PUBLISHED.setdefault(sid, set()).add(request['requestId'])
    return request
_BACKFILLED = False
# Hooks that sit in front of every reply wait at most this long for the asks DB, never the store's 30 s default.
HOOK_DB_TIMEOUT = 5


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

# Runtime-written user rows (Kanban notices, auto-continue): a turn they start was not started by the user.
_RUNTIME_KINDS = frozenset({"kanban_notice", "auto_continue"})
AUTOMATIC_PREFIX = "[Automatic notice, not typed by the user] "


def _turn_message(user_message: Any, conversation_history: Any) -> str:
    """The turn's message as the extractor sees it: labeled automatic when the newest user row is a runtime row
    (``display_kind``), so a notice can never read as the user answering an open step (core._runtime_message)."""
    text = user_message if isinstance(user_message, str) else ""
    for msg in reversed(conversation_history if isinstance(conversation_history, list) else []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            if (msg.get("display_kind") or "") in _RUNTIME_KINDS and not text.startswith(AUTOMATIC_PREFIX):
                return AUTOMATIC_PREFIX + text
            break
    return text


def pre_llm_call(session_id: str = "", user_message: str = "", platform: str = "", parent_session_id: str = "",
                 conversation_history: Any = None, **_: Any) -> dict | None:
    if not _eligible(session_id, platform, parent_session_id):
        return None
    try:
        _TURN_PUBLISHED.pop(session_id, None)  # a new turn: earlier turns' publications are no provenance
        _USER_MSG[session_id] = _turn_message(user_message, conversation_history)
        own = core.server_owner(session_id)
        st = core.ActionStore(timeout=HOOK_DB_TIMEOUT)
        global _BACKFILLED
        if not _BACKFILLED:
            _BACKFILLED = True
            try:
                st.backfill_legacy(_ledger(), gateway=own['gatewayId'])
            except Exception:
                log.warning("user-steps legacy backfill deferred")
        chat.reconcile(core, st, own, aux_llm)
        lg = _ledger()
        for step in lg.read(session_id).get('steps', []):
            if step.get('session') == session_id and step.get('status') == 'open':
                st.migrate_step(own, step)
        try:  # retire asks whose linked work settled before listing them to the agent
            core.reconcile_moot(st, lg, own)
        except Exception:
            log.exception("user-steps moot reconciliation failed")
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
                'not from this list. Legacy steps and extracted text cannot grant permission. '
                'If one is no longer needed (work finished, answered in chat, superseded), withdraw it with '
                'action_request {"withdraw": {"requestId": "<id>", "reason": "<why>"}} so it leaves the '
                'user\'s For you queue.]\n'
                + json.dumps(pending)) if pending else ''
        incomplete = [r for r in pending if r['kind'] == 'approval' and not r['scope']['complete']]
        if incomplete:
            # Missing plans are agent work: the user has no button for them.
            note += ('\n[Agent work: these approvals lack a complete plan, so the user cannot decide them. '
                     'If still needed, publish action_request with the same requestId and expectedRevision, '
                     'the FULL exact plan, scope, consequences and approve/change/decline choices. Do not ask '
                     'the user to request a plan. If it is no longer needed, withdraw it instead. Similar titles are not '
                     'duplicates: withdraw one only when you know it asks the same decision.] ' + json.dumps([{'requestId': r['requestId'],
                                                                     'expectedRevision': r['revision'],
                                                                     'title': r['title']} for r in incomplete]))
        gone = st.unnotified_withdrawals(own)
        gone_note = ('[No longer needed: these requests were withdrawn without any decision (no approval, no '
                     'permission). Do not act on them as approved; re-ask only if still genuinely required.]\n'
                     + '\n'.join(f"- {w['title']} (withdrawn by {w['withdrawal']['by']}: {w['withdrawal']['reason']})"
                                 for w in gone)) if gone else ''
        if gone:
            st.mark_notified(own, [w['requestId'] for w in gone])
        context = '\n'.join(x for x in (note, gone_note, receipts) if x)
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
            # Own try block: a lookup failure (e.g. "database is locked") must not skip recording this
            # reply's asks below (self-heal plugin-dca5d02c20).
            try:
                chat.reconcile(core, core.ActionStore(timeout=HOOK_DB_TIMEOUT), action_owner, aux_llm)
            except Exception as exc:
                log.warning("user-steps reconcile failed; still recording this reply's asks: %r", exc)
                _trace(hook="transform", session=session_id, platform=platform, reconcile_error=repr(exc)[:300])
        user_msg = _USER_MSG.pop(session_id, "")
        out = core.process_reply(
            response_text, session_id, user_msg=user_msg, llm=aux_llm, ledger=_ledger(),
            cards=None, framed=_framed(session_id),
            action_store=core.ActionStore(timeout=HOOK_DB_TIMEOUT) if action_owner else None,
            action_owner=action_owner, turn_requests=_TURN_PUBLISHED.pop(session_id, set()),
            session_ref=f"@session:{_profile()}/{session_id}", card_asks=_card_asks(session_id, user_msg))
        if out.get("error"):
            log.warning("user-steps extractor fell back to regex: %s", out["error"])
        if out.get("dropped"):
            log.info("user-steps dropped %d terminal-doable step(s): %s", len(out["dropped"]), out["dropped"])
        _trace(hook="transform", session=session_id, platform=platform, source=out.get("source"),
               new=[s["id"] for s in out.get("steps", [])], resolved=out.get("resolved"),
               framed=out.get("text") is not None, error=out.get("error"), card_asks=out.get("card_asks"),
               dropped_terminal=out.get("dropped") or None)
        return out.get("text")
    except Exception as exc:
        _trace(hook="transform", session=session_id, platform=platform, exception=repr(exc)[:300])
        log.exception("user-steps transform failed")
        return None  # fail open: the reply is shown unchanged


def _card_asks(session_id: str, user_msg: str) -> list[dict]:
    """Blocked cards' own asks this reply may relay (core.kanban_asks). A failed read lists none: fail open."""
    try:
        return core.kanban_asks(session_id, user_msg)
    except Exception as exc:
        _trace(hook="card_asks", session=session_id, error=repr(exc)[:300])
        return []


def publication_buffer(session_id='', platform='', final_response='', attempt=0,
                       hold_budget=2, **_: Any):
    """Return missing plan work to the agent, never to a user-facing button."""
    if not _eligible(session_id, platform) or not core._worth_asking(final_response):
        return None
    owner = core.server_owner(session_id)
    store = core.ActionStore(timeout=HOOK_DB_TIMEOUT)
    # The open asks go to the extractor and the matcher: a reply restating a published approval in other
    # words is covered by it. Without them every paraphrase held the reply and pushed the agent to publish
    # the same approval again (hub chat 67cb2d).
    open_asks = core.open_asks(store, owner, _ledger().read(session_id).get('steps', []))
    # continuity=True: the listed titles must not silently drop a re-ask before covered() judges it.
    # Other chats' titles are relay context only in a turn a runtime notice started (relay provenance); in a
    # turn the user started an overlapping title elsewhere must not drop this reply's approval from the gate.
    relay = core._runtime_message(_USER_MSG.get(session_id, ''))
    # A blocked card's own ask is a request too: a reply relaying it needs no new approval (no hold, no nudge to
    # publish a second, unlinked one).
    card_asks = _card_asks(session_id, _USER_MSG.get(session_id, ''))
    res = core.extract(final_response, [{'id': a['id'], 'kind': a['kind'], 'text': a['title']} for a in open_asks]
                       + [{'id': e['id'], 'kind': e['kind'], 'text': core.card_ask_listing(e)} for e in card_asks],
                       '', aux_llm, continuity=True, elsewhere=core.elsewhere_titles(store, owner) if relay else [])
    approvals = [s for s in res.get('steps', []) if s['kind'] == 'approve']
    if not approvals:
        return None
    # An uncorroborated model repeat maps nothing (core.split_repeats): the other rules judge that sentence.
    tiles = {a['id']: core.tile_texts(a) for a in open_asks}
    tiles.update({a['step']['id']: core.tile_texts(a) for a in open_asks if a.get('step') and a['step'].get('id')})
    tiles.update({e['id']: core.tile_texts(e) for e in card_asks})
    res['repeats'] = core.split_repeats(res.get('repeats') or (), tiles, final_response)[0]
    ready = {r['requestId'] for r in store.requests(owner)
             if r['kind'] == 'approval' and r['state'] == 'open' and r['scope']['complete'] and not r.get('resolution')
             and {'approve', 'revise', 'decline'} <= {c['effect'] for c in r['choices']}}
    mapped: dict = {}

    def covered(step):
        if core.card_ask_for(step, card_asks, res.get('repeats') or (), final_response) is not None:
            return True
        same = core.same_open_ask(step, open_asks, res.get('repeats') or (), mapped,
                                  _TURN_PUBLISHED.get(session_id, ()), reply=final_response)
        if same is not None:
            mapped.setdefault(core.ask_key(step.get('verbatim_span')), same)
        return same is not None and same['id'] in ready

    if all([covered(s) for s in approvals]):
        return None
    if attempt >= hold_budget:
        return {'replace': 'The approval request is not ready: the agent has not provided a complete exact plan. No approval is requested and no permission has been granted.'}
    return {'hold': True, 'nudge': 'Complete the missing agent work now: publish action_request with the FULL EXACT inline plan, scope, consequences, limits, exclusions and approve/change/decline choices. If an open request already asks this, revise THAT one (its requestId and expectedRevision) instead of creating another; a matching title alone does not make it the same request. Do not ask the user to request a plan, and do not invent approval. If this is only a clarification, publish kind choice/input with concrete answer labels instead. Then present the decision.'}


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


_ALIAS_SHAPE = ('objects {gatewayId, profile, type, id, board?} (type message, user_step or kanban; board only '
                'for kanban); pass [] when there are none')


def _shape_check(payload: dict) -> None:
    """Reject wrongly shaped containers before anything indexes into them, with the exact fix.

    The handler reads aliases/choices/scope before core.validate_action runs, so a string where an object
    belongs used to crash with AttributeError instead of returning an error the agent could act on.
    """
    def objects(value, name, shape):
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise core.ActionError(f'{name} must be a list of {shape}')
    if 'scope' in payload and not isinstance(payload['scope'], dict):
        raise core.ActionError('scope must be an object {markdown, complete, sourceRefs}')
    if isinstance(payload.get('scope'), dict) and 'sourceRefs' in payload['scope']:
        objects(payload['scope']['sourceRefs'], 'scope.sourceRefs', _ALIAS_SHAPE)
    if 'aliases' in payload:
        objects(payload['aliases'], 'aliases', _ALIAS_SHAPE)
    if 'choices' in payload:
        objects(payload['choices'], 'choices', 'objects {id, label, effect} (effect approve, revise, decline or '
                'answer; optional explain, recommended)')


def publish_action_request(args: dict, session_id: str = '', **_) -> str:
    """Agent producer: explicit scope only. Extraction NEVER calls this complete-plan path.

    session_id is injected by tool dispatch; no owner/session payload is accepted.
    Existing IDs require expectedRevision and publish a new immutable scope revision.
    `withdraw` retires one of THIS session's undecided requests that is no longer needed;
    it is not a decision and grants nothing.
    """
    import json
    import uuid
    try:
        if isinstance(args, dict) and 'withdraw' in args:
            core._object(args, ('withdraw',))
            w = args['withdraw']
            core._object(w, ('requestId', 'reason'), ('expectedRevision',))
            core._string(w['requestId'])
            owner = core.server_owner(session_id)
            out = core.withdraw_request(core.ActionStore(), _ledger(), owner, w['requestId'], by='agent',
                                        reason=w['reason'], expected_revision=w.get('expectedRevision'),
                                        cards=cards_mod.QualifiedCardDelivery())
            return json.dumps({'request': out})
        core._object(args, ('request',), ('requestId', 'expectedRevision', 'decisionCard'))
        owner = core.server_owner(session_id)
        if not isinstance(args['request'], dict):
            raise core.ActionError('request must be an object (the ActionRequest v2 fields)')
        payload = dict(args['request'])
        _shape_check(payload)
        st = core.ActionStore()
        if core.declares_no_ask(payload):
            # "No decision needed: ..." is a status note, not an ask: it must leave For you, not become a tile.
            if not args.get('requestId'):
                raise core.ActionError('Nothing to ask: this request says no decision is needed, so it is not '
                                       'published. If an open request is moot, withdraw it with '
                                       '{"withdraw": {"requestId": "<id>", "reason": "<why>"}}.')
            if 'expectedRevision' not in args: raise core.ActionError('expectedRevision required')
            out = core.withdraw_request(st, _ledger(), owner, args['requestId'], by='agent',
                                        reason=payload.get('title') or 'No decision needed',
                                        expected_revision=args.get('expectedRevision'),
                                        cards=cards_mod.QualifiedCardDelivery())
            return json.dumps({'request': out, 'note': 'Not published: it says no decision is needed, so the '
                               'request was withdrawn instead (no decision, nothing granted).'})
        if payload.get('kind') == 'approval' and not (payload.get('scope') or {}).get('complete'):
            raise core.ActionError('Complete the full exact inline plan, scope and consequences before publishing approval. This is agent work; do not ask the user to request a plan.')
        if payload.get('kind') == 'approval' and not {'approve', 'revise', 'decline'} <= {
                c.get('effect') for c in payload.get('choices', [])}:
            raise core.ActionError('Provide approve, revise and decline choices with their exact outcomes.')
        if any(key in payload for key in ('owner', 'requestId', 'revision', 'resolution')):
            raise core.ActionError('Producer identity is server-owned')
        rid = args.get('requestId') or str(uuid.uuid4())
        payload.update(owner=owner, requestId=rid, revision=1)
        adapter = cards_mod.QualifiedCardDelivery()
        # Aliases alone never authorize adopting a card. Only producer-created
        # cards survive this path; replacement plans preserve the original binding.
        refs = [ref for ref in payload.get('aliases', []) if ref.get('type') == 'kanban']
        if args.get('requestId'):
            if 'expectedRevision' not in args: raise core.ActionError('expectedRevision required')
            if 'decisionCard' in args: raise core.ActionError('Cannot add a card to a replacement')
            current = st.get(owner, rid)
            old_refs = [ref for ref in current['aliases'] if ref['type'] == 'kanban']
            if refs != old_refs: raise core.ActionError('Replacement must retain exact producer card aliases')
            # A decided card was archived: the replacement asks again on a fresh card (appended alias).
            fresh = adapter.replacement_card(current, payload) if old_refs else None
            if fresh: payload['aliases'] = list(payload.get('aliases', [])) + [fresh]
            try:
                out = st.revise(owner, rid, args['expectedRevision'], payload, bind=adapter.bind_request)
            except Exception:
                if fresh:
                    try: adapter.discard(fresh, owner)
                    except Exception: pass  # stays inert; a repeat publish reuses it via the creation key
                raise
        else:
            if 'expectedRevision' in args: raise core.ActionError('New requests have no expectedRevision')
            if refs: raise core.ActionError('Card aliases must be created by decisionCard, not supplied')
            # Identity is provenance, never the title. A new publication revises an open request in place only
            # when it cites that request's own extracted step (an exact qualified user_step ref of this owner in
            # aliases or scope.sourceRefs), or re-sends the exact same request (a retry). An equal title alone is
            # a different request: publish it and name the same-title ones so the agent can withdraw a replaced
            # one itself. Nothing here withdraws another request.
            def qualified(ref):
                return (ref.get('gatewayId'), ref.get('profile'), ref.get('type'), str(ref.get('id')))
            scope_obj = payload.get('scope') if isinstance(payload.get('scope'), dict) else None
            cited = {qualified(ref) for ref in list(payload.get('aliases') or []) + list((scope_obj or {}).get('sourceRefs') or [])
                     if isinstance(ref, dict) and ref.get('type') == 'user_step'
                     and ref.get('gatewayId') == owner['gatewayId'] and ref.get('profile') == owner['profile']}
            undecided = [] if 'decisionCard' in args else [
                r for r in st.requests(owner)
                if r['state'] in ('open', 'needs_context') and not r.get('resolution')
                and not any(a['type'] == 'kanban' for a in r['aliases'])]
            fold = [r for r in undecided if cited & {qualified(a) for a in r['aliases'] if a['type'] == 'user_step'}]
            if len(fold) > 1:
                raise core.ActionError('scope.sourceRefs/aliases cite steps of more than one open request; publish '
                                       'one request per decision (revise each by its requestId).')
            body_keys = ('schemaVersion', 'kind', 'title', 'scope', 'limits', 'exclusions', 'outcomes', 'choices',
                         'state')
            probe = core.validate_action(json.loads(json.dumps(payload)))
            exact = [r for r in undecided if r not in fold and all(r.get(k) == probe.get(k) for k in body_keys)
                     and all(a in r['aliases'] for a in probe.get('aliases') or [])]
            if not fold and exact:
                # The same request re-sent (lost response, retried tool call): nothing new to publish.
                out = _published(owner, exact[-1])
                return json.dumps({'request': out, 'note': f"Already open with exactly this content: "
                                   f"{out['requestId']} revision {out['revision']}. Nothing new was published."})
            key = core.ask_key(payload.get('title'))
            others = [r for r in undecided if r not in fold and len(key) >= 20 and core.ask_key(r['title']) == key]
            note = None
            if fold:
                target = fold[0]
                payload.update(requestId=target['requestId'],
                               aliases=target['aliases'] + [a for a in payload.get('aliases', [])
                                                            if a not in target['aliases']])
                try:
                    out = st.revise(owner, target['requestId'], target['revision'], payload,
                                    bind=adapter.bind_request, undecided=True)
                except core.ActionError as exc:
                    if exc.status != 409: raise
                    if str(exc) == 'Stale revision':
                        # Revised concurrently, still undecided: never publish a second tile for the same source
                        # step. Hand the agent the request to revise by its id.
                        raise core.ActionError(f"The open request this cites ({target['requestId']}) changed "
                                               "meanwhile (stale revision); revise it by requestId with its "
                                               "current expectedRevision.", 409) from exc
                    if str(exc) not in ('A decision is already recorded for this revision',
                                        'Decision delivery is pending'):
                        raise  # e.g. an alias that belongs to another request: report it as is
                    # A decision was recorded or is being delivered: the decision wins. Publish a separate
                    # request that does not reuse the decided request's refs.
                    payload.update(requestId=rid, aliases=[a for a in payload['aliases'] if a not in target['aliases']])
                    if isinstance(payload.get('scope'), dict):
                        payload['scope'] = {**payload['scope'], 'sourceRefs': [
                            ref for ref in payload['scope'].get('sourceRefs') or [] if ref not in target['aliases']]}
                    fold = []
                else:
                    note = (f"Revised in place the open request whose step this cites: {target['requestId']} "
                            f"revision {out['revision']}.")
                    if others:
                        note += (' Also open with the same title (separate requests): '
                                 + ', '.join(r['requestId'] for r in others) + '; withdraw any this replaces.')
                    return json.dumps({'request': _published(owner, out), 'note': note})
            if others:
                note = ('Published as a separate request. Also open with the same title: '
                        + ', '.join(r['requestId'] for r in others) + '. A matching title does not make them one '
                        'request: if this replaces one, withdraw it (or revise it by its requestId and '
                        'expectedRevision instead of publishing a new one).')
            payload = core.validate_action(payload)
            if 'decisionCard' in args:
                core._object(args['decisionCard'], ('board',))
                ref = adapter.prepare(payload, args['decisionCard']['board'])
                payload['aliases'].append(ref)
            out = st.create(owner, payload, bind=adapter.bind_request)
            if note:
                return json.dumps({'request': _published(owner, out), 'note': note})
        return json.dumps({'request': _published(owner, out)})
    except (ValueError, TypeError, RuntimeError) as exc:
        return json.dumps({'error': str(exc), 'status': getattr(exc, 'status', 422)})
    except (AttributeError, KeyError) as exc:
        # Backstop: a shape _shape_check/validate_action missed must still reach the agent as a fixable error,
        # never a raw dispatch failure it can only retry blindly.
        return json.dumps({'error': f'Malformed request ({type(exc).__name__}: {exc}). Check the field shapes '
                                    'in the action_request schema.', 'status': 422})


PRODUCER_SCHEMA = {
    'name': 'action_request',
    'description': 'Publish a complete, inline ActionRequest v2 for the current session. If David already asked for this change in this chat (or standing approval covers it), do it; don\'t publish an approval. Include exact scope, limits, exclusions, outcomes and explicit choices. No execution occurs. To replace scope, reuse requestId with expectedRevision. Never claim completeness for an inferred or missing plan. A new request with the same title as an open one is a separate request; revise an existing one by its requestId and expectedRevision. Never publish a "no decision needed" request: withdraw instead. To retire one of this session\'s undecided requests that is no longer needed (work finished, answered in chat, superseded), send only {"withdraw": {"requestId", "reason"}}; withdrawal is not a decision and grants nothing.',
    'parameters': {'type': 'object', 'additionalProperties': False,
                   'properties': {'request': {'type': 'object', 'description': 'ActionRequest v2 fields except server-owned owner, requestId, revision and resolution. Required: schemaVersion=2, kind, title, scope {markdown,complete,sourceRefs}, limits, exclusions, outcomes {revise,approve?,decline?}, limits [{key, value}] (objects, optional permissionRequired: true/false), exclusions [plain strings], choices [{id, label, effect, explain?, recommended?}] (each choice an object {id, label, effect}, effect approve/revise/decline/answer; approval needs all of approve, revise and decline; explain = one plain line on what happens if the user picks it, e.g. cost and what resumes; recommended: true on at most one choice, your pick), aliases: [] unless reusing a request\'s own refs. Identity is never the title: a new request with an open request\'s title is published as a separate request. To supply the plan for an open extracted tile, revise it by requestId and expectedRevision, or cite its user_step ref in scope.sourceRefs (each alias and sourceRef is an object {gatewayId, profile, type, id, board?} with type message, user_step or kanban (kanban requires board), never a bare card id string), state (open or needs_context).'},
                                  'requestId': {'type': 'string'}, 'expectedRevision': {'type': 'integer'},
                                  'decisionCard': {'type': 'object', 'additionalProperties': False,
                                                   'properties': {'board': {'type': 'string'}}, 'required': ['board'],
                                                   'description': 'Optional new dedicated blocked decision metadata card. Never an existing card ID.'},
                                  'withdraw': {'type': 'object', 'additionalProperties': False,
                                               'properties': {'requestId': {'type': 'string'}, 'reason': {'type': 'string'},
                                                              'expectedRevision': {'type': 'integer'}},
                                               'required': ['requestId', 'reason'],
                                               'description': 'Use alone (no request). Removes this session\'s undecided request from the user\'s For you queue; refused once a decision is recorded.'}}}}


_EARLY_DISPOSE = None


def _register_clarify_early() -> None:
    """Hand recorded decisions to a session blocked in clarify (core tools/clarify_early.py). Older cores lack it."""
    global _EARLY_DISPOSE
    try:
        from tools import clarify_early as core_early  # type: ignore
    except ImportError:
        return
    try:
        from . import clarify_early as provider_mod
    except ImportError:
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location("user_steps_clarify_early", Path(__file__).with_name("clarify_early.py"))
        provider_mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(provider_mod)  # type: ignore[union-attr]
    if _EARLY_DISPOSE:
        _EARLY_DISPOSE()
    _EARLY_DISPOSE = core_early.register_provider(provider_mod.make_provider())


def register(ctx) -> None:
    _require_publication_contract()
    _register_clarify_early()
    ctx.register_hook("pre_final_gate", publication_buffer)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("transform_llm_output", transform_llm_output)
    ctx.register_tool(name='action_request', toolset='user_steps', schema=PRODUCER_SCHEMA,
                      handler=publish_action_request, description=PRODUCER_SCHEMA['description'])
