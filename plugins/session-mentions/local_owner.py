"""Same-gateway transport through the authenticated canonical owner, never a second executor.

Discovery does not start/restart a service. Old/unavailable owners and foreign references keep
Desktop routing. Once this transport claims a request, no error falls through to Desktop.
"""
from __future__ import annotations

import asyncio
import re


def prepare(action, args, rf):
    if action not in {"read", "send"}:
        return None
    text = str(args.get("target") or "").strip()
    # Match only the documented forms; malformed/quoted/stacked references retain Desktop parsing.
    match = re.fullmatch(r"(?:@session:)?([\w.-]+)(?:@([\w.-]+))?/([\w-]+)", text)
    if match:
        profile, route, sid = match.groups()
    else:
        match = re.fullmatch(r"(?:([\w.-]+):)?([\w-]+)", text)
        if not match:
            return None
        route, sid = match.groups()
        profile = "default"
    info = rf.device_info()
    mine = {info.get("device"), info.get("gateway"), *(info.get("aliases") or [])} - {None, ""}
    if route and (rf.norm_name(route) in rf.LOCAL_ALIASES or rf.norm_name(route) not in mine):
        return None
    try:
        from hermes_cli.profiles import get_profile_dir, validate_profile_name
        from hermes_cli.gateway_runtime import discover_gateway_endpoint
        # An older disk client cannot safely operate this transport; keep Desktop usable.
        from hermes_cli.gateway_client import (GatewayClient, GatewayRPCError, GATEWAY_WS_PROTOCOL,
                                               _session_ticket, gateway_ws_target)
        # Validate, do not normalize a path-bearing token into another profile.
        validate_profile_name(profile)
        home = get_profile_dir(profile)
        found = discover_gateway_endpoint(home)
        if found.state != "ready" or found.endpoint is None:
            return None
        return home, found.endpoint, profile, sid
    except (ImportError, OSError, ValueError):
        return None


def run(target, req):
    return asyncio.run(deliver(target, req))


async def deliver(target, req, *, connect=None):
    """Transport adapters are injectable; production uses the private-control ticket's WS.

    Only a received pre-submit refusal proves retry safety. A dropped submit acknowledgement
    remains unknown and retains its dedupe reservation, even when the owner actually admitted it.
    """
    from hermes_cli.gateway_client import GatewayRPCError
    home, endpoint, profile, sid = target
    submitted = False
    accepted = None
    started = asyncio.get_running_loop().time()
    base = {"gateway": req.get("gateway_name") or "", "profile": profile,
            "session_id": sid, "ref": f"@session:{profile}/{sid}", "transport": "local_owner"}
    try:
        async with asyncio.timeout(400):
            async with (connect or _connect)(home, endpoint) as client:
                if req["action"] == "read":
                    page = await client.rpc("session.transcript", session_id=sid,
                                            limit=_limit(req["args"].get("limit")), order="latest", inline_images=False)
                    return {**base, "ok": True, "messages": _messages(page),
                            "has_more": bool(page.get("next_cursor")), "title": ""}
                sender = req.get("sender") or {}
                ref = f"@session:{sender.get('profile') or 'default'}/{sender['session_id']}" if sender.get("session_id") else ""
                header = ("[Message from another Hermes session"
                          + (f' "{sender["title"]}"' if sender.get("title") else "")
                          + (f" ({ref})" if ref else "") + "]")
                resumed = await client.rpc("session.resume", session_id=sid, omit_messages=True)
                live = resumed["session_id"]
                # Set before the await: any transport exception may follow a committed admission.
                if _stop_waiting():
                    raise ValueError("sender stopped before submit")
                submitted = True
                try:
                    receipt = await client.rpc("prompt.submit", session_id=live, submission_id=req["id"],
                                               text=header + "\n\n" + str(req["args"]["message"]), queued=True)
                except GatewayRPCError as exc:
                    # Some RPC errors arise after commit (e.g. storage failure publishing
                    # the pending queue). Only these contract checks prove non-admission.
                    submitted = str(exc) not in {"invalid_params", "permission_denied", "session_busy",
                                                 "not_found", "profile_mismatch"}
                    raise
                admission = receipt.get("admission_id")
                if not admission or receipt.get("status") not in {"queued", "started", "terminal"}:
                    raise ValueError("unconfirmed admission")
                out = {**base, "session_id": live, "ok": True, "delivered": True,
                       "status": "queued", "admission_id": admission, "request_id": req["id"]}
                accepted = out
                asked = max(0, int(req.get("wait_for_reply") or 0))
                wait = min(asked, max(0, int(390 - (asyncio.get_running_loop().time() - started))))
                if not asked:
                    return out
                deadline = asyncio.get_running_loop().time() + wait
                # Read the exact admission result, never a previous/later turn's transcript reply.
                while True:
                    if _stop_waiting():
                        return {**out, "reply": None, "note": "Stopped waiting; admission remains live. Do not resend."}
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        return {**out, "reply_wait_seconds": wait, "reply": None, "reply_timed_out": True}
                    try:
                        async with asyncio.timeout(remaining):
                            receipt = await client.rpc("prompt.receipt", session_id=live, admission_id=admission,
                                                       include_result=True)
                            if receipt.get("status") == "terminal":
                                reply = (receipt.get("result") or {}).get("final_response")
                                return {**out, "status": "replied" if reply else "completed",
                                        "reply_wait_seconds": wait, "reply": reply or None}
                            await asyncio.sleep(min(.5, remaining))
                    except TimeoutError:
                        return {**out, "reply_wait_seconds": wait, "reply": None, "reply_timed_out": True}
    except Exception as exc:
        if not submitted and isinstance(exc, GatewayRPCError) and str(exc) == "unknown_method":
            return None  # proven pre-submit incompatibility: hand our claim back to Desktop
        # Bounded reason only: do not export token-bearing connection/exception text.
        reason = str(exc) if isinstance(exc, GatewayRPCError) else type(exc).__name__
        if accepted is not None:
            return {**accepted, "reply": None, "reason": reason,
                    "note": "Admission confirmed; reply unavailable. Read the target; do not resend."}
        return {**base, "ok": False, "delivered": False, "status": "unknown" if submitted else "refused",
                "retry_safe": not submitted, "request_id": req["id"], "reason": reason,
                "error": ("Local owner acknowledgement is unknown; read the target before resending."
                          if submitted else "Local gateway owner refused the request; nothing was submitted.")}


def _stop_waiting():
    try:
        import threading
        from tools.interrupt import is_interrupted, consume_yield
        return bool(is_interrupted() or consume_yield(threading.get_ident()))
    except ImportError:
        return False


def _limit(value):
    try:
        return max(1, min(200, int(value or 40)))
    except (TypeError, ValueError):
        return 40


def _messages(page):
    rows = page.get("rows") or []
    out = []
    for row in rows:
        text = row.get("text")
        if row.get("role") in {"user", "assistant"} and isinstance(text, str) and text.strip():
            out.append({"id": row.get("row_id"), "role": row["role"], "at": row.get("timestamp"),
                        "content": text[:4000] + ("…[truncated]" if len(text) > 4000 else "")})
    return out


from contextlib import asynccontextmanager


@asynccontextmanager
async def _connect(home, endpoint):
    from hermes_cli.gateway_client import GatewayClient, GATEWAY_WS_PROTOCOL, _session_ticket, gateway_ws_target
    from websockets.asyncio.client import connect
    ticket = await asyncio.to_thread(_session_ticket, home, endpoint, refuse_restart_wait=True)
    url, protocols = gateway_ws_target(endpoint, ticket)
    async with connect(url, subprotocols=protocols, open_timeout=10, close_timeout=2,
                       max_size=8 * 1024 * 1024, proxy=None) as ws:
        if ws.subprotocol != GATEWAY_WS_PROTOCOL:
            raise ValueError("gateway protocol mismatch")
        async with GatewayClient(ws) as client:
            yield client
