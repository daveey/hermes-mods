#!/usr/bin/env python3
"""pool_llm: run script / eval / batch LLM calls on the Hermes subscription account pool, not paid API keys.

Hermes already holds several Claude Max (``anthropic`` OAuth) and ChatGPT (``openai-codex`` OAuth) accounts in its
credential pool. Scripts that call the Anthropic or OpenAI SDK with a paid org key stop dead when that key runs out of
credit; this module gives them the pool instead, and moves to the next account when one hits its limit (429/quota).

Must run inside the Hermes runtime (it imports ``agent.*``). From a shell or a foreign venv use the launcher
``bin/hermes-pool-llm``, which re-execs this file under the Hermes interpreter:

  hermes-pool-llm ask --provider anthropic --model claude-sonnet-5-5 "question"     # one call, text on stdout
  hermes-pool-llm ask --provider openai --tier fast --json - < prompt.txt            # JSON {text, provider, model, route}
  hermes-pool-llm models                                                             # pins the pool may serve
  hermes-pool-llm check                                                              # one ping per pool, exit 0 = ok
  hermes-pool-llm serve --port 8765                                                  # SDK-compatible local endpoint

``serve`` lets an unmodified harness use the pool: set ``ANTHROPIC_BASE_URL=http://127.0.0.1:8765`` and
``OPENAI_BASE_URL=http://127.0.0.1:8765/v1`` (any non-empty API key). It answers Anthropic ``POST /v1/messages`` on the
Claude pool and OpenAI ``POST /v1/responses`` / ``POST /v1/chat/completions`` on the Codex pool. Every response carries
``x-hermes-pool-route`` (provider, model, account label) so a run can record the served path.

Library use (Hermes interpreter): ``complete(messages, provider="anthropic", model=...)`` -> dict, or
``client("anthropic")`` -> object with ``.chat.completions.create(**kw)`` (OpenAI-shaped).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from typing import Any, Callable, Optional

PROVIDERS = {"anthropic": "anthropic", "claude": "anthropic", "openai": "openai-codex", "gpt": "openai-codex",
             "codex": "openai-codex", "openai-codex": "openai-codex"}
TIERS = ("deep", "standard", "fast")
# Fallback when the managed routing registry is off; the registry, when on, is the source of truth.
DEFAULT_MODELS = {("anthropic", "deep"): "claude-opus-5-5", ("anthropic", "standard"): "claude-sonnet-5-5",
                  ("anthropic", "fast"): "claude-haiku-5-5", ("openai-codex", "deep"): "gpt-6-astra",
                  ("openai-codex", "standard"): "gpt-6.1-sol", ("openai-codex", "fast"): "gpt-6-luna"}

# An account that is out of quota / rate limited: rotate. Anything else (bad request, auth) is raised as is.
QUOTA_TEXT = re.compile(r"credit balance is too low|no credits remaining|insufficient_quota|rate.?limit|usage limit"
                        r"|quota|too many requests|overloaded|extra usage", re.I)
ROTATE_STATUS = {402, 429, 529}
CC_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."
MCP_PREFIX = "mcp__"


class PoolExhausted(RuntimeError):
    """Every account in the pool refused the call for quota/rate reasons."""


def canonical_provider(provider: str) -> str:
    p = (provider or "").strip().lower()
    if p not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; use one of {sorted(set(PROVIDERS))}")
    return PROVIDERS[p]


def is_quota_error(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int) and status in ROTATE_STATUS:
        return True
    return bool(QUOTA_TEXT.search(str(exc)))


def with_rotation(call: Callable[[Any], Any], pool: Any, *, model: Optional[str] = None,
                  max_tries: Optional[int] = None, on_attempt: Optional[Callable[[Any], None]] = None) -> Any:
    """Run ``call(entry)`` on the pool's selected account; on a quota/rate error mark that account exhausted and
    retry on the next one, at most once per account. Raises PoolExhausted when no account is left."""
    tries = max_tries or max(1, len(list(pool.entries())))
    last: Optional[BaseException] = None
    tried: list[str] = []
    for _ in range(tries):
        entry = pool.select(model=model) if model else pool.select()
        if entry is None or getattr(entry, "id", None) in tried:
            break
        tried.append(getattr(entry, "id", "?"))
        if on_attempt:
            on_attempt(entry)
        try:
            return call(entry)
        except Exception as exc:  # noqa: BLE001 - classified below
            if not is_quota_error(exc):
                raise
            last = exc
            status = getattr(exc, "status_code", None)
            pool.mark_exhausted_and_rotate(status_code=status if isinstance(status, int) else 429,
                                           credential_id=getattr(entry, "id", None), model=model,
                                           error_context={"reason": "quota", "message": str(exc)[:300]})
    labels = ", ".join(tried) or "none available"
    raise PoolExhausted(f"every pooled account refused (tried: {labels}); last error: {last}") from last


# ---------------------------------------------------------------------------------------------- runtime pieces --
def managed_pins() -> Optional[dict]:
    """{(provider, tier): model} from the managed routing registry, or None when it is off."""
    try:
        from agent.model_routing import eligible_routes
        from agent.model_routing_managed import managed_config
        cfg = managed_config()
    except Exception:
        return None
    if not cfg:
        return None
    policy = cfg["model_routing"]
    out: dict = {}
    for level in policy.get("routes", {}):
        for route in eligible_routes(policy, level):
            if route.get("billing") == "subscription":
                out.setdefault((route["provider"], level), route["model"])
    return out


def resolve_model(provider: str, model: Optional[str] = None, tier: Optional[str] = None) -> str:
    provider = canonical_provider(provider)
    pins = managed_pins() or DEFAULT_MODELS
    allowed = sorted({m for (p, _), m in pins.items() if p == provider})
    if model:
        if managed_pins() is not None and model not in allowed:
            raise ValueError(f"{model!r} is not a subscription model the pool serves for {provider}; "
                             f"use one of {allowed} (or --tier {'/'.join(TIERS)})")
        return model
    tier = tier or "standard"
    if (provider, tier) not in pins:
        raise ValueError(f"no {tier} model for {provider}; choose from {allowed}")
    return pins[(provider, tier)]


def load_pool(provider: str):
    from agent.credential_pool import load_pool as _load
    pool = _load(canonical_provider(provider))
    if not pool.has_credentials():
        raise PoolExhausted(f"no {provider} accounts in the Hermes pool (hermes auth list)")
    return pool


def entry_label(entry: Any) -> str:
    return str(getattr(entry, "label", "") or getattr(entry, "id", "?"))


def anthropic_client(entry: Any):
    from agent.anthropic_adapter import build_anthropic_client
    return build_anthropic_client(entry.runtime_api_key, getattr(entry, "runtime_base_url", None))


def codex_client(entry: Any):
    token = entry.runtime_api_key
    try:
        from agent.auxiliary_client import _codex_cloudflare_headers, _create_openai_client
        from hermes_cli.auth_codex import _codex_pool_route_base_url
        base = _codex_pool_route_base_url(getattr(entry, "runtime_base_url", None))
        return _create_openai_client(api_key=token, base_url=base,
                                     default_headers=_codex_cloudflare_headers(token, base_url=base))
    except ImportError:
        from openai import OpenAI
        return OpenAI(api_key=token, base_url="https://chatgpt.com/backend-api/codex")


def oauth_messages_body(body: dict) -> tuple[dict, dict]:
    """Anthropic Messages body -> what a Claude Max OAuth token accepts: Claude Code system prefix first, tool
    names on the ``mcp__`` wire form (plain names bill as a third-party app). Returns (body, wire->original)."""
    b = json.loads(json.dumps(body))
    names: dict[str, str] = {}

    def wire(name: str) -> str:
        w = name if name.startswith(MCP_PREFIX) else MCP_PREFIX + name
        names[w] = name
        return w

    system = b.get("system")
    if isinstance(system, str):
        system = [{"type": "text", "text": system}] if system else []
    system = system if isinstance(system, list) else []
    if not (system and isinstance(system[0], dict) and system[0].get("text") == CC_PREFIX):
        system = [{"type": "text", "text": CC_PREFIX}] + system
    b["system"] = system
    for tool in b.get("tools") or []:
        if isinstance(tool, dict) and isinstance(tool.get("name"), str) and tool.get("type") in (None, "custom"):
            tool["name"] = wire(tool["name"])
    for msg in b.get("messages") or []:
        for block in msg.get("content") if isinstance(msg.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("name"), str):
                block["name"] = wire(block["name"])
    tc = b.get("tool_choice")
    if isinstance(tc, dict) and tc.get("type") == "tool" and isinstance(tc.get("name"), str):
        tc["name"] = wire(tc["name"])
    b.pop("stream", None)
    return b, names


def restore_tool_names(resp: dict, names: dict) -> dict:
    for block in resp.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in names:
            block["name"] = names[block["name"]]
    return resp


CODEX_DROP = ("max_output_tokens", "temperature", "top_p", "max_tokens", "metadata", "user", "service_tier")


def codex_responses_body(body: dict) -> dict:
    b = {k: v for k, v in body.items() if k not in CODEX_DROP}
    b["store"] = False
    b.setdefault("instructions", "")
    b.pop("stream", None)
    if isinstance(b.get("input"), str):
        b["input"] = [{"role": "user", "content": b["input"]}]
    return b


def anthropic_messages(body: dict, *, pool=None, route: Optional[dict] = None) -> dict:
    pool = pool or load_pool("anthropic")
    wire_body, names = oauth_messages_body(body)

    def call(entry):
        if route is not None:
            route.update(provider="anthropic", model=body.get("model"), account=entry_label(entry))
        resp = anthropic_client(entry).messages.create(**wire_body)
        return restore_tool_names(resp.model_dump(exclude_none=True), names)

    return with_rotation(call, pool)


def codex_responses(body: dict, *, pool=None, route: Optional[dict] = None) -> dict:
    pool = pool or load_pool("openai-codex")
    wire_body = codex_responses_body(body)

    def call(entry):
        if route is not None:
            route.update(provider="openai-codex", model=body.get("model"), account=entry_label(entry))
        with codex_client(entry).responses.stream(**wire_body) as stream:
            final = stream.get_final_response()
        return final.model_dump(exclude_none=True)

    return with_rotation(call, pool)


def _chat_to_anthropic(messages: list, max_tokens: int, model: str) -> dict:
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system" and isinstance(m.get("content"), str))
    rest = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") in ("user", "assistant")]
    body = {"model": model, "max_tokens": max_tokens, "messages": rest}
    if system:
        body["system"] = system
    return body


def _chat_to_responses(messages: list, model: str) -> dict:
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system" and isinstance(m.get("content"), str))
    rest = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") in ("user", "assistant")]
    return {"model": model, "input": rest, "instructions": system}


def complete(messages: list, *, provider: str = "anthropic", model: Optional[str] = None, tier: Optional[str] = None,
             max_tokens: int = 4096) -> dict:
    """One chat turn on the pool. Returns {text, provider, model, route}."""
    provider = canonical_provider(provider)
    model = resolve_model(provider, model, tier)
    route: dict = {}
    if provider == "anthropic":
        resp = anthropic_messages(_chat_to_anthropic(messages, max_tokens, model), route=route)
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    else:
        resp = codex_responses(_chat_to_responses(messages, model), route=route)
        text = "".join(c.get("text", "") for it in resp.get("output", []) if it.get("type") == "message"
                       for c in it.get("content", []) if c.get("type") == "output_text")
    return {"text": text, "provider": provider, "model": model, "route": route}


class _Completions:
    def __init__(self, provider, model, tier):
        self._p, self._m, self._t = provider, model, tier

    def create(self, *, messages, model=None, max_tokens=None, max_completion_tokens=None, **_ignored):
        from types import SimpleNamespace as NS
        r = complete(messages, provider=self._p, model=model or self._m, tier=self._t,
                     max_tokens=max_tokens or max_completion_tokens or 4096)
        return NS(model=r["model"], route=r["route"],
                  choices=[NS(index=0, finish_reason="stop", message=NS(role="assistant", content=r["text"]))])


def client(provider: str = "anthropic", *, model: Optional[str] = None, tier: Optional[str] = None):
    """OpenAI-shaped client (``.chat.completions.create(messages=...)``) on the pool for ``provider``."""
    from types import SimpleNamespace as NS
    return NS(chat=NS(completions=_Completions(canonical_provider(provider), model, tier)))


# ------------------------------------------------------------------------------------------------------ serve --
def _chat_completions(body: dict, route: dict) -> dict:
    model = body.get("model") or resolve_model("openai-codex")
    provider = "anthropic" if str(model).startswith("claude") else "openai-codex"
    r = complete(body.get("messages") or [], provider=provider, model=model,
                 max_tokens=body.get("max_tokens") or body.get("max_completion_tokens") or 4096)
    route.update(r["route"])
    return {"id": "pool-chat", "object": "chat.completion", "model": r["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": r["text"]}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    pools: dict = {}
    lock = threading.Lock()

    def pool_for(p):
        with lock:
            if p not in pools:
                pools[p] = load_pool(p)
            return pools[p]

    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            sys.stderr.write("pool-llm: " + (fmt % args) + "\n")

        def _send(self, code, payload, route=None):
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            if route:
                self.send_header("x-hermes-pool-route", json.dumps(route))
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.rstrip("/") in ("", "/health"):
                return self._send(200, {"ok": True})
            self._send(404, {"error": {"type": "not_found", "message": self.path}})

        def do_POST(self):
            route: dict = {}
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                path = self.path.split("?")[0].rstrip("/")
                if body.get("stream"):
                    raise ValueError("stream=true is not supported by hermes-pool-llm serve; call without streaming")
                if path.endswith("/messages"):
                    resolve_model("anthropic", body.get("model"))
                    out = anthropic_messages(body, pool=pool_for("anthropic"), route=route)
                elif path.endswith("/responses"):
                    resolve_model("openai-codex", body.get("model"))
                    out = codex_responses(body, pool=pool_for("openai-codex"), route=route)
                elif path.endswith("/chat/completions"):
                    out = _chat_completions(body, route)
                else:
                    return self._send(404, {"error": {"type": "not_found", "message": path}})
                self._send(200, out, route)
            except ValueError as exc:
                self._send(400, {"type": "error", "error": {"type": "invalid_request_error", "message": str(exc)}})
            except PoolExhausted as exc:
                self._send(429, {"type": "error", "error": {"type": "rate_limit_error", "message": str(exc)}}, route)
            except Exception as exc:  # noqa: BLE001 - upstream error, pass status through
                status = getattr(exc, "status_code", None)
                self._send(status if isinstance(status, int) and 400 <= status < 600 else 502,
                           {"type": "error", "error": {"type": "api_error", "message": str(exc)[:2000]}}, route)

    srv = ThreadingHTTPServer((host, port), H)
    print(f"hermes-pool-llm serving on http://{host}:{port} (ANTHROPIC_BASE_URL=http://{host}:{port} "
          f"OPENAI_BASE_URL=http://{host}:{port}/v1)", file=sys.stderr, flush=True)
    srv.serve_forever()


# -------------------------------------------------------------------------------------------------------- CLI --
def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="hermes-pool-llm", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="one call on the pool")
    a.add_argument("prompt", help="prompt text, or - for stdin")
    a.add_argument("--provider", default="anthropic")
    a.add_argument("--model")
    a.add_argument("--tier", choices=TIERS)
    a.add_argument("--system", default="")
    a.add_argument("--max-tokens", type=int, default=4096)
    a.add_argument("--json", action="store_true", help="print {text, provider, model, route}")
    sub.add_parser("models", help="subscription models the pool serves")
    sub.add_parser("check", help="one ping per pool; exit 0 when both answer")
    s = sub.add_parser("serve", help="local Anthropic/OpenAI-compatible endpoint on the pool")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)

    if args.cmd == "models":
        pins = managed_pins() or DEFAULT_MODELS
        for (p, t), m in sorted(pins.items()):
            print(f"{p:13} {t:9} {m}")
        return 0
    if args.cmd == "serve":
        serve(args.host, args.port)
        return 0
    if args.cmd == "check":
        ok = True
        for p in ("anthropic", "openai-codex"):
            try:
                r = complete([{"role": "user", "content": "Reply with exactly: pong"}], provider=p, tier="fast",
                             max_tokens=20)
                print(f"{p}: ok ({r['model']} via {r['route'].get('account')}): {r['text'].strip()[:40]}")
            except Exception as exc:  # noqa: BLE001
                ok = False
                print(f"{p}: FAILED {type(exc).__name__}: {str(exc)[:300]}")
        return 0 if ok else 1
    prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
    msgs = ([{"role": "system", "content": args.system}] if args.system else []) + [{"role": "user", "content": prompt}]
    try:
        r = complete(msgs, provider=args.provider, model=args.model, tier=args.tier, max_tokens=args.max_tokens)
    except (ValueError, PoolExhausted) as exc:
        print(f"hermes-pool-llm: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(r) if args.json else r["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
