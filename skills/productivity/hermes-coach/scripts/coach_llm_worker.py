#!/usr/bin/env python3
"""coach_llm_worker.py: bounded single-request model calls for coach_eval.py, run under the Hermes interpreter.

coach_eval.py (stdlib only) starts this with the interpreter and source root of the installed `hermes` launcher
(or COACH_HERMES_PYTHON / COACH_HERMES_ROOT), so credentials, OAuth headers and the price table come from Hermes
itself and never pass through coach_eval. Protocol: one JSON object per stdin line, one JSON reply per stdout line.

  {"op": "price", "model": M}  -> {"input", "output", "cache_write", "cache_read", "request", "source", "prefix"}
                                  ($ per million tokens; request = $ per request; prefix = system text the route
                                  prepends, e.g. the Claude Code identity line on an OAuth route)
  {"op": "call", "model": M, "system": S, "user": U, "max_tokens": N[, "thinking": {"type": "disabled"}]}
                               -> {"text", "usage": {input_tokens, output_tokens, cache_write_tokens,
                                   cache_read_tokens}} or {"error": "..."}

Each "call" is exactly one billable provider request (a model that rejects {"thinking": {"type": "disabled"}}
answers with an unbilled 400 naming its no-thinking type, which is then sent instead): the SDK client is built with max_retries=0, no tools, no thinking,
and an explicit max_tokens (thinking tokens count against it), so a request's cost is bounded by (input tokens + max_tokens) x the price table.
Provider: COACH_EVAL_PROVIDER (default anthropic; only the Anthropic Messages wire is supported).
"""
import json
import os
import re
import sys


def _reply(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from agent.usage_pricing import get_pricing_entry
    from agent import anthropic_adapter as aa
    from agent.anthropic_credentials import anthropic_route_is_oauth

    provider = os.environ.get("COACH_EVAL_PROVIDER") or "anthropic"
    rt = resolve_runtime_provider(requested=provider)
    if rt.get("api_mode") != "anthropic_messages":
        _reply({"fatal": f"provider {provider} uses {rt.get('api_mode')}; only anthropic_messages is supported"})
        return 2
    key, base = rt.get("api_key"), rt.get("base_url")
    client = aa.build_anthropic_client(key, base, timeout=300).with_options(max_retries=0)
    oauth = bool(anthropic_route_is_oauth(base, key, provider=rt.get("provider")))
    prefix = aa._CLAUDE_CODE_SYSTEM_PREFIX if oauth else ""
    del key
    no_think_alias = {}
    _reply({"ready": True, "provider": rt.get("provider"), "oauth": oauth})
    for line in sys.stdin:
        try:
            req = json.loads(line)
            if req.get("op") == "price":
                e = get_pricing_entry(req["model"], provider=rt.get("provider"), base_url=base)
                if e is None or e.input_cost_per_million is None or e.output_cost_per_million is None:
                    _reply({"error": f"no price for {req['model']}"})
                    continue
                def f(name):  # tiered prices: the bound uses the higher (above-threshold) rate
                    vals = [getattr(e, name, None), getattr(e, name.replace("_per_million", "_per_million_above"), None)]
                    vals = [float(v) for v in vals if v is not None]
                    return max(vals) if vals else None
                _reply({"input": f("input_cost_per_million"), "output": f("output_cost_per_million"),
                        "cache_write": f("cache_write_cost_per_million"), "cache_read": f("cache_read_cost_per_million"),
                        "request": float(e.request_cost or 0), "source": f"{e.source} {e.pricing_version or ''}".strip(),
                        "prefix": prefix})
            elif req.get("op") == "call":
                system = []
                if prefix:
                    system.append({"type": "text", "text": prefix})
                system.append({"type": "text", "text": req["system"], "cache_control": {"type": "ephemeral"}})
                extra = {"thinking": req["thinking"]} if req.get("thinking") else {}
                if extra and no_think_alias.get(req["model"]):
                    extra = {"thinking": no_think_alias[req["model"]]}
                try:
                    resp = client.messages.create(model=req["model"], max_tokens=int(req["max_tokens"]), system=system,
                                                  messages=[{"role": "user", "content": req["user"]}], **extra)
                except Exception as exc:
                    # Newer models reject {"type": "disabled"} with an unbilled 400 naming the type to send instead.
                    m = re.search(r'send \\?"thinking\\?": \{\\?"type\\?": \\?"(\w+)\\?"\}', str(exc))
                    if not (extra and req["thinking"].get("type") == "disabled" and m and "400" in str(exc)):
                        raise
                    no_think_alias[req["model"]] = {"type": m.group(1)}
                    resp = client.messages.create(model=req["model"], max_tokens=int(req["max_tokens"]), system=system,
                                                  messages=[{"role": "user", "content": req["user"]}],
                                                  thinking=no_think_alias[req["model"]])
                text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
                u = resp.usage
                _reply({"text": text, "stop_reason": resp.stop_reason, "usage": {
                    "input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
                    "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                    "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}})
            else:
                _reply({"error": "unknown op"})
        except Exception as exc:  # one failed request: report it, never retry
            _reply({"error": f"{type(exc).__name__}: {str(exc)[:300]}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
