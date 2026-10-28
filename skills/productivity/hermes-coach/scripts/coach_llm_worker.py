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
Provider: COACH_EVAL_PROVIDER (default anthropic). Two wires: Anthropic Messages (anthropic_messages) and the Codex
Responses wire (codex_responses, e.g. openai-codex / gpt-6-astra). A request may carry "provider" to use another
provider than the default (the judge runs on anthropic while a codex model is replayed); backends are built lazily.

Codex wire: the ChatGPT Codex endpoint rejects max_output_tokens, so there the cap is enforced client-side: the stream
is closed (stop_reason "max_tokens") once the streamed output exceeds 4 chars per allowed token. Routes that accept
max_output_tokens (api.openai.com) get it on the wire. No tools, no retries (max_retries=0), no reasoning field sent.
The price op returns the price table's rates; a subscription ("included-route") model prices at $0.
"""
import json
import os
import re
import sys


def _reply(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _get(o, name, default=None):
    v = o.get(name) if isinstance(o, dict) else getattr(o, name, None)
    return default if v is None else v


def _price_reply(e, prefix):
    if e is None or e.input_cost_per_million is None or e.output_cost_per_million is None:
        return None

    def f(name):  # tiered prices: the bound uses the higher (above-threshold) rate
        vals = [getattr(e, name, None), getattr(e, name.replace("_per_million", "_per_million_above"), None)]
        vals = [float(v) for v in vals if v is not None]
        return max(vals) if vals else None
    return {"input": f("input_cost_per_million"), "output": f("output_cost_per_million"),
            "cache_write": f("cache_write_cost_per_million"), "cache_read": f("cache_read_cost_per_million"),
            "request": float(e.request_cost or 0), "source": f"{e.source} {e.pricing_version or ''}".strip(),
            "prefix": prefix}


class AnthropicBackend:
    def __init__(self, client, prefix, provider, base_url, get_price):
        self.client, self.prefix, self.provider, self.base_url, self.get_price = client, prefix, provider, base_url, get_price
        self.no_think_alias = {}

    def price(self, model):
        return _price_reply(self.get_price(model, self.provider, self.base_url), self.prefix) \
            or {"error": f"no price for {model}"}

    def call(self, req):
        client, prefix, no_think_alias = self.client, self.prefix, self.no_think_alias
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
        return {"text": text, "stop_reason": resp.stop_reason, "usage": {
            "input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
            "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}}


def _item_texts(item):
    """output_text parts of a finished Responses `message` item."""
    if _get(item, "type", "") != "message":
        return []
    return [_get(c, "text", "") for c in _get(item, "content", []) or [] if _get(c, "type", "") == "output_text"]


class CodexBackend:
    """Codex Responses wire: ONE streaming responses.create per call, instructions = system, no tools, store=False,
    no retries. `client` is an openai.OpenAI built with max_retries=0 (tests inject a fake)."""
    CHARS_PER_TOKEN = 4  # client-side output cap where the endpoint takes no max_output_tokens

    def __init__(self, client, provider, base_url, get_price):
        self.client, self.provider, self.base_url, self.get_price = client, provider, base_url, get_price
        self.chatgpt_backend = "chatgpt.com" in str(base_url or "")

    def price(self, model):
        return _price_reply(self.get_price(model, self.provider, self.base_url), "") or {"error": f"no price for {model}"}

    def call(self, req):
        max_tokens = int(req["max_tokens"])
        kw = {"model": req["model"], "instructions": req["system"], "store": False, "stream": True,
              "input": [{"role": "user", "content": [{"type": "input_text", "text": req["user"]}]}]}
        if not self.chatgpt_backend:  # the ChatGPT Codex endpoint 400s on max_output_tokens
            kw["max_output_tokens"] = max_tokens
        stream = self.client.responses.create(**kw)
        parts, done, chars, usage, stop, seen = [], [], 0, {}, "end_turn", {}
        try:
            for ev in stream:
                t = _get(ev, "type", "")
                seen[t] = seen.get(t, 0) + 1
                if t == "response.output_item.done":  # the final message, for streams that carry no text deltas
                    done += _item_texts(_get(ev, "item", {}))
                if t == "response.output_text.delta":
                    d = _get(ev, "delta", "")
                    parts.append(d)
                    chars += len(d)
                elif t in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                    chars += len(_get(ev, "delta", ""))
                elif t == "response.refusal.delta":
                    stop = "refusal"
                elif t in ("response.failed", "error"):
                    err = _get(_get(ev, "response", {}), "error", None) or _get(ev, "error", None) or _get(ev, "message", ev)
                    raise RuntimeError(f"codex stream {t}: {str(_get(err, 'message', err))[:200]}")
                elif t in ("response.completed", "response.incomplete"):
                    r = _get(ev, "response", {})
                    if not done:
                        for it in _get(r, "output", None) or []:
                            done += _item_texts(it)
                    u = _get(r, "usage", {})
                    cached = int(_get(_get(u, "input_tokens_details", {}), "cached_tokens", 0) or 0)
                    usage = {"input_tokens": max(0, int(_get(u, "input_tokens", 0) or 0) - cached),
                             "output_tokens": int(_get(u, "output_tokens", 0) or 0),
                             "cache_write_tokens": 0, "cache_read_tokens": cached}
                    if t == "response.incomplete":
                        stop = "max_tokens"
                    break
                if chars > max_tokens * self.CHARS_PER_TOKEN:
                    stop = "max_tokens"
                    break
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        if not usage:  # closed early or no terminal frame: bill the cap, not zero
            usage = {"input_tokens": 0, "output_tokens": max_tokens if stop == "max_tokens" else chars // self.CHARS_PER_TOKEN,
                     "cache_write_tokens": 0, "cache_read_tokens": 0}
        text = "".join(parts) or "".join(done)
        if not text.strip() and stop == "end_turn" and os.environ.get("COACH_WORKER_DEBUG_FILE"):
            with open(os.environ["COACH_WORKER_DEBUG_FILE"], "a") as fh:
                fh.write(json.dumps({"empty_reply_events": seen, "usage": usage}) + "\n")
        return {"text": "" if stop == "refusal" else text, "stop_reason": stop, "usage": usage}


def build_backend(provider):
    """The backend for `provider` from Hermes credentials: (backend, hello-info) or raises SystemExit-able ValueError."""
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from agent.usage_pricing import get_pricing_entry
    rt = resolve_runtime_provider(requested=provider)
    mode, key, base = rt.get("api_mode"), rt.get("api_key"), rt.get("base_url")

    def get_price(model, prov, url):
        return get_pricing_entry(model, provider=prov, base_url=url)
    if mode == "anthropic_messages":
        from agent import anthropic_adapter as aa
        from agent.anthropic_credentials import anthropic_route_is_oauth
        client = aa.build_anthropic_client(key, base, timeout=300).with_options(max_retries=0)
        oauth = bool(anthropic_route_is_oauth(base, key, provider=rt.get("provider")))
        be = AnthropicBackend(client, aa._CLAUDE_CODE_SYSTEM_PREFIX if oauth else "", rt.get("provider"), base, get_price)
        return be, {"provider": rt.get("provider"), "oauth": oauth}
    if mode == "codex_responses":
        from openai import OpenAI
        headers = {}
        if "chatgpt.com" in str(base or ""):
            from agent.codex_headers import codex_cloudflare_headers
            headers = codex_cloudflare_headers(key, base_url=base)
        client = OpenAI(api_key=key, base_url=base, timeout=300, max_retries=0, default_headers=headers or None)
        return CodexBackend(client, rt.get("provider"), base, get_price), {"provider": rt.get("provider"), "oauth": False}
    raise ValueError(f"provider {provider} uses {mode}; only anthropic_messages and codex_responses are supported")


def serve(default_provider, backends, build=build_backend, lines=None):
    """JSON-lines loop. `backends` {provider: backend} may be pre-seeded (tests); others are built lazily."""
    for line in (sys.stdin if lines is None else lines):
        try:
            req = json.loads(line)
            prov = req.get("provider") or default_provider
            if prov not in backends:
                backends[prov] = build(prov)[0]
            be = backends[prov]
            if req.get("op") == "price":
                _reply(be.price(req["model"]))
            elif req.get("op") == "call":
                _reply(be.call(req))
            else:
                _reply({"error": "unknown op"})
        except Exception as exc:  # one failed request: report it, never retry
            _reply({"error": f"{type(exc).__name__}: {str(exc)[:300]}"})
    return 0


def main():
    provider = os.environ.get("COACH_EVAL_PROVIDER") or "anthropic"
    try:
        be, info = build_backend(provider)
    except ValueError as exc:
        _reply({"fatal": str(exc)})
        return 2
    _reply(dict(info, ready=True))
    return serve(provider, {provider: be})


if __name__ == "__main__":
    sys.exit(main())
