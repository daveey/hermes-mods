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

``serve`` lets an unmodified harness use the pool: run ``eval "$(hermes-pool-llm env)"`` to export
numeric loopback URLs and this home's private API token. It answers Anthropic ``POST /v1/messages`` on the
Claude pool and OpenAI ``POST /v1/responses`` / ``POST /v1/chat/completions`` on the Codex pool, and ``POST /v1/embeddings`` via OpenRouter. Every response carries
``x-hermes-pool-route`` (provider, model, account label) so a run can record the served path.

Library use (Hermes interpreter): ``complete(messages, provider="anthropic", model=...)`` -> dict, or
``client("anthropic")`` -> object with ``.chat.completions.create(**kw)`` (OpenAI-shaped).
"""
from __future__ import annotations

import argparse
import hmac
import os
from pathlib import Path
import secrets
import shlex
import stat
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

# Quota failures rotate immediately; auth failures get one store resync/refresh before rotation.
AUTH_TEXT = re.compile(r"token has been revoked|invalid_grant", re.I)
QUOTA_TEXT = re.compile(r"credit balance is too low|no credits remaining|insufficient_quota|rate.?limit|usage limit"
                        r"|quota|too many requests|overloaded|extra usage", re.I)
ROTATE_STATUS = {402, 429, 529}
CC_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."
MCP_PREFIX = "mcp__"


class PoolExhausted(RuntimeError):
    """Every account refused the call for quota/rate or unrecovered auth reasons."""


def canonical_provider(provider: str) -> str:
    p = (provider or "").strip().lower()
    if p not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; use one of {sorted(set(PROVIDERS))}")
    return PROVIDERS[p]


def error_status(exc: BaseException) -> Optional[int]:
    return getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)


def is_auth_error(exc: BaseException) -> bool:
    return error_status(exc) == 401 or bool(AUTH_TEXT.search(str(exc)))


def is_quota_error(exc: BaseException) -> bool:
    status = error_status(exc)
    if isinstance(status, int) and status in ROTATE_STATUS:
        return True
    return bool(QUOTA_TEXT.search(str(exc)))


def with_rotation(call: Callable[[Any], Any], pool: Any, *, model: Optional[str] = None,
                  max_tries: Optional[int] = None, on_attempt: Optional[Callable[[Any], None]] = None) -> Any:
    """Rotate on quota or unrecovered auth failure, visiting each account at most once.

    Auth failures first resync/refresh the issuing credential via the pool's locked
    recovery API and retry once with a newly built client. No daemon restart needed.
    """
    tries = max_tries or max(1, len(list(pool.entries())))
    last: Optional[BaseException] = None
    tried: list[str] = []
    for _ in range(tries):
        entry = pool.select(model=model) if model else pool.select()
        if entry is None or getattr(entry, "id", None) in tried:
            break
        tried.append(getattr(entry, "id", "?"))
        for attempt in range(2):
            if on_attempt:
                on_attempt(entry)
            try:
                return call(entry)
            except Exception as exc:  # noqa: BLE001 - classified below
                auth = is_auth_error(exc)
                if not auth and not is_quota_error(exc):
                    raise
                last = exc
                if auth and attempt == 0:
                    try:
                        refreshed = pool.try_refresh_matching(credential_id=entry.id)
                    except Exception:  # failed refresh must not strand other accounts
                        refreshed = None
                    if refreshed is not None and refreshed.id == entry.id:
                        entry = refreshed
                        continue
                # Auth is account-wide, even if the upstream used status 400 for
                # invalid_grant. Use the pool's 401 cooldown, not a quota cooldown.
                status = error_status(exc)
                pool.mark_exhausted_and_rotate(status_code=401 if auth else status if isinstance(status, int) else 429,
                                               credential_id=getattr(entry, "id", None), model=model,
                                               error_context={"reason": "auth" if auth else "quota",
                                                              "message": str(exc)[:300]})
                break
    labels = ", ".join(tried) or "none available"
    raise PoolExhausted(f"every pooled account refused (tried: {labels}); last error: {last}") from last


# ---------------------------------------------------------------------------------------------- runtime pieces --
def managed_routes() -> Optional[dict]:
    """Ordered subscription models by (provider, tier), or None when the registry is off."""
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
                models = out.setdefault((route["provider"], level), [])
                if route["model"] not in models:
                    models.append(route["model"])
    return out


def managed_pins() -> Optional[dict]:
    """First subscription model per (provider, tier), preserving the legacy shape."""
    routes = managed_routes()
    return None if routes is None else {key: models[0] for key, models in routes.items()}


def resolve_model(provider: str, model: Optional[str] = None, tier: Optional[str] = None) -> str:
    provider = canonical_provider(provider)
    routes = managed_routes()
    if routes is None:
        routes = {key: [value] for key, value in DEFAULT_MODELS.items()}
        managed = False
    else:
        managed = True
    allowed = sorted({m for (p, _), models in routes.items() if p == provider for m in models})
    if model:
        if managed and model not in allowed:
            raise ValueError(f"{model!r} is not a subscription model the pool serves for {provider}; "
                             f"use one of {allowed} (or --tier {'/'.join(TIERS)})")
        return model
    tier = tier or "standard"
    if (provider, tier) not in routes:
        raise ValueError(f"no {tier} model for {provider}; choose from {allowed}")
    return routes[(provider, tier)][0]


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

    # Scope selection to the requested model: an unscoped select treats ANY model's cooldown as blocking, so one
    # benched model (e.g. a weekly Fable cap on every account) made the whole pool look exhausted for Haiku/Sonnet.
    return with_rotation(call, pool, model=body.get("model"))


def codex_responses(body: dict, *, pool=None, route: Optional[dict] = None) -> dict:
    pool = pool or load_pool("openai-codex")
    wire_body = codex_responses_body(body)

    def call(entry):
        if route is not None:
            route.update(provider="openai-codex", model=body.get("model"), account=entry_label(entry))
        with codex_client(entry).responses.stream(**wire_body) as stream:
            final = stream.get_final_response()
        return final.model_dump(exclude_none=True)

    return with_rotation(call, pool, model=body.get("model"))


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
             max_tokens: int = 4096, thinking: Optional[dict] = None) -> dict:
    """One chat turn on the pool. Returns {text, provider, model, route}, plus Anthropic stop_reason.

    ``thinking`` is the Anthropic ``thinking`` field (e.g. ``{"type": "disabled"}`` for a short classifier answer,
    which adaptive thinking would otherwise eat); other providers ignore it."""
    provider = canonical_provider(provider)
    resolved = resolve_model(provider, model, tier)
    models = [resolved] if model else (managed_routes() or {}).get((provider, tier or "standard"), [resolved])
    failed: list[str] = []
    for candidate in models:
        route: dict = {}
        try:
            if provider == "anthropic":
                body = _chat_to_anthropic(messages, max_tokens, candidate)
                if thinking is not None:
                    body["thinking"] = thinking
                resp = anthropic_messages(body, route=route)
                text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
            else:
                resp = codex_responses(_chat_to_responses(messages, candidate), route=route)
                text = "".join(c.get("text", "") for it in resp.get("output", []) if it.get("type") == "message"
                               for c in it.get("content", []) if c.get("type") == "output_text")
        except PoolExhausted as exc:
            failed.append(candidate)
            if len(failed) == len(models):
                raise PoolExhausted(f"every subscription model refused (tried: {', '.join(failed)}); {exc}") from exc
            continue
        result = {"text": text, "provider": provider, "model": candidate, "route": route,
                  "fallback_from": failed[0] if failed else None}
        if provider == "anthropic":
            result["stop_reason"] = resp.get("stop_reason")
        return result
    raise PoolExhausted(f"no subscription models available for {provider}/{tier or 'standard'}")


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
def _chat_tools_to_anthropic(body: dict, model: str) -> dict:
    """Translate the non-streaming OpenAI function-tool protocol without losing history."""
    messages = body.get("messages") or []
    out = _chat_to_anthropic([], body.get("max_tokens") or body.get("max_completion_tokens") or 4096, model)
    if "thinking" in body:
        out["thinking"] = body["thinking"]
    system = []
    for msg in messages:
        role, content = msg.get("role"), msg.get("content")
        if role in ("system", "developer"):
            if not isinstance(content, str):
                raise ValueError("tool chat system/developer content must be text")
            system.append(content)
            continue
        if role == "tool":
            if not isinstance(content, str):
                raise ValueError("tool result content must be text")
            block = {"type": "tool_result", "tool_use_id": msg["tool_call_id"], "content": content}
            previous = out["messages"][-1] if out["messages"] else None
            if previous and previous["role"] == "user" and all(
                b.get("type") == "tool_result" for b in previous["content"]):
                previous["content"].append(block)
            else:
                out["messages"].append({"role": "user", "content": [block]})
            continue
        if role not in ("user", "assistant"):
            raise ValueError(f"unsupported tool chat role: {role!r}")
        if content is None:
            blocks = []
        elif isinstance(content, str):
            blocks = [{"type": "text", "text": content}] if content else []
        elif isinstance(content, list) and all(isinstance(b, dict) and b.get("type") == "text" for b in content):
            blocks = [dict(b) for b in content]
        else:
            raise ValueError("tool chat content supports text only")
        for call in msg.get("tool_calls") or []:
            if role != "assistant" or call.get("type") != "function":
                raise ValueError("only assistant function tool_calls are supported")
            function = call["function"]
            arguments = json.loads(function["arguments"])
            if not isinstance(arguments, dict):
                raise ValueError("function arguments must encode a JSON object")
            blocks.append({"type": "tool_use", "id": call["id"], "name": function["name"], "input": arguments})
        if not blocks:
            raise ValueError("tool chat messages need text or tool_calls")
        out["messages"].append({"role": role, "content": blocks})
    if system:
        out["system"] = "\n\n".join(system)
    if "tools" in body:
        tools = []
        for tool in body["tools"] or []:
            if tool.get("type") != "function":
                raise ValueError("only function tools are supported")
            function = tool["function"]
            tools.append({"name": function["name"], "description": function.get("description", ""),
                          "input_schema": function.get("parameters", {"type": "object", "properties": {}})})
        if tools:
            out["tools"] = tools
    choice = body.get("tool_choice")
    if choice is not None:
        if isinstance(choice, str) and choice in ("auto", "none", "required"):
            out["tool_choice"] = {"type": "any" if choice == "required" else choice}
        elif isinstance(choice, dict) and choice.get("type") == "function":
            out["tool_choice"] = {"type": "tool", "name": choice["function"]["name"]}
        else:
            raise ValueError("unsupported tool_choice")
    if "parallel_tool_calls" in body and choice != "none":
        out.setdefault("tool_choice", {"type": "auto"})["disable_parallel_tool_use"] = not body["parallel_tool_calls"]
    return out


def _anthropic_tool_chat_reply(resp: dict, model: str) -> dict:
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    calls = [{"id": b["id"], "type": "function", "function": {
        "name": b["name"], "arguments": json.dumps(b["input"])}}
        for b in resp.get("content", []) if b.get("type") == "tool_use"]
    message = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = calls
    usage = resp.get("usage") or {}
    prompt = sum(usage.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    completion = usage.get("output_tokens", 0)
    chat_usage = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
    output_details = usage.get("output_tokens_details") or {}
    if "thinking_tokens" in output_details:
        chat_usage["completion_tokens_details"] = {"reasoning_tokens": output_details["thinking_tokens"]}
    finish = {"tool_use": "tool_calls", "max_tokens": "length"}.get(resp.get("stop_reason"), "stop")
    return {"id": resp.get("id", "pool-chat"), "object": "chat.completion", "model": resp.get("model", model),
            "choices": [{"index": 0, "finish_reason": finish, "message": message}],
            "usage": chat_usage}


def _chat_completions(body: dict, route: dict) -> dict:
    model = body.get("model") or resolve_model("openai-codex")
    provider = "anthropic" if str(model).startswith("claude") else "openai-codex"
    messages = body.get("messages") or []
    if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
        raise ValueError("messages must be a list of objects")
    tool_request = "tools" in body or "tool_choice" in body or any(
        m.get("role") == "tool" or m.get("tool_calls") for m in messages)
    if tool_request:
        if provider != "anthropic":
            raise ValueError("tools are not supported on this route")
        model = resolve_model(provider, model)
        try:
            translated = _chat_tools_to_anthropic(body, model)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"invalid function tool request: {exc}") from exc
        return _anthropic_tool_chat_reply(anthropic_messages(translated, route=route), model)
    r = complete(body.get("messages") or [], provider=provider, model=model,
                 max_tokens=body.get("max_tokens") or body.get("max_completion_tokens") or 4096)
    route.update(r["route"])
    return {"id": "pool-chat", "object": "chat.completion", "model": r["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": r["text"]}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


class UpstreamError(Exception):
    """Non-2xx answer from an upstream passthrough; ``status_code`` is passed on to the caller."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"


def _openrouter_key() -> str:
    from hermes_cli.config import get_env_value
    return get_env_value("OPENROUTER_API_KEY") or ""


def _http_post(url: str, headers: dict, data: bytes) -> tuple:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def embeddings(body: dict, *, get_key: Optional[Callable[[], str]] = None, post: Optional[Callable] = None) -> dict:
    """POST /v1/embeddings: passthrough to OpenRouter (the account pool has no embeddings backend)."""
    asked = body.get("model")
    if not asked or "input" not in body:
        raise ValueError("embeddings needs 'model' and 'input'")
    key = (get_key or _openrouter_key)()
    if not key:
        raise UpstreamError(401, "OPENROUTER_API_KEY is not set in Hermes's .env")
    up = dict(body)
    up["model"] = asked if "/" in asked else f"openai/{asked}"
    status, raw = (post or _http_post)(OPENROUTER_EMBEDDINGS_URL,
                                       {"content-type": "application/json", "authorization": f"Bearer {key}"},
                                       json.dumps(up).encode())
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    if not 200 <= status < 300:
        raise UpstreamError(status if 400 <= status < 600 else 502, text.replace(key, "***")[:2000])
    try:
        out = json.loads(text)
    except ValueError:
        raise UpstreamError(502, "non-JSON embeddings response: " + text.replace(key, "***")[:300])
    if not isinstance(out, dict) or not out.get("data"):
        raise UpstreamError(502, "embeddings response has no data: " + text.replace(key, "***")[:500])
    out["model"] = asked
    out.setdefault("object", "list")
    out.setdefault("usage", {"prompt_tokens": 0, "total_tokens": 0})
    return out


def serve_token() -> str:
    """Load/create this home's private persistent HTTP credential, without following links."""
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    directory = home / "pool-llm"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Pin the directory for every later operation (also reject a directory symlink).
    dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(dfd)
        if info.st_uid != os.getuid():
            raise ValueError("serve token directory must be owned by the current uid")
        os.fchmod(dfd, 0o700)
        try:
            fd = os.open("serve.token", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=dfd)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                stream.write(secrets.token_urlsafe(32))
                stream.flush()
                os.fsync(stream.fileno())
        info = os.lstat("serve.token", dir_fd=dfd)
        def validate(value):
            if (not stat.S_ISREG(value.st_mode) or value.st_uid != os.getuid()
                    or value.st_mode & 0o077):
                raise ValueError("serve token must be a regular file owned by the current uid with no group/other permissions")
        validate(info)
        fd = os.open("serve.token", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        with os.fdopen(fd, "r", encoding="ascii") as stream:
            opened = os.fstat(stream.fileno())
            validate(opened)
            if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError("serve token changed while opening")
            token = stream.read(4097)
        if not token or len(token) > 4096 or re.fullmatch(r"[A-Za-z0-9_-]+", token) is None:
            raise ValueError("serve token has invalid contents; use a private token_urlsafe token")
        return token
    except (OSError, UnicodeError) as exc:
        raise ValueError("cannot safely read/create serve token; check pool-llm/serve.token ownership and permissions") from exc
    finally:
        os.close(dfd)


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    token = serve_token().encode("ascii")
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import ipaddress
    pools: dict = {}
    lock = threading.Lock()

    def pool_for(p):
        with lock:
            if p not in pools:
                pools[p] = load_pool(p)
            return pools[p]

    class H(BaseHTTPRequestHandler):
        def parse_request(self):
            if not super().parse_request():
                return False
            # No browser callers: Origin includes "null" and empty values. Host
            # must name the actual bound loopback socket, never a DNS alias or a
            # client/forwarded header. Check before reading a body or loading pools.
            address, bound_port = self.server.server_address[:2]
            authority = f"[{address}]:{bound_port}" if ":" in address else f"{address}:{bound_port}"
            if (not ipaddress.ip_address(address).is_loopback
                    or self.headers.get_all("Host", []) != [authority]
                    or self.headers.get_all("Origin") is not None):
                self.close_connection = True
                self._send(403, {"error": {"type": "permission_error",
                                          "message": "local non-browser requests only"}})
                return False
            presented = list(self.headers.get_all("x-api-key", []))
            for value in self.headers.get_all("Authorization", []):
                parts = value.split()
                presented.append(parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else "")
            if not presented or not all(hmac.compare_digest(value.encode("utf-8"), token) for value in presented):
                self.close_connection = True
                self._send(401, {"error": {"type": "authentication_error",
                    "message": "Invalid or missing pool token; use hermes-pool-llm env to configure the client"}})
                return False
            return True

        def log_message(self, fmt, *args):
            # Do not log caller-controlled request lines/headers, even on parse errors.
            sys.stderr.write("pool-llm: HTTP request handled\n")

        def _send(self, code, payload, route=None):
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            if route:
                self.send_header("x-hermes-pool-route", json.dumps(route))
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _stream_messages(self, body, pool, route):
            # Prime inside rotation: opening HTTP alone does not establish that an
            # account can serve. After the first event, never replay the request.
            wire_body, names = oauth_messages_body(body)
            wire_body["stream"] = True

            def open_stream(entry):
                client = anthropic_client(entry)
                stream = None
                try:
                    stream = client.messages.create(**wire_body)
                    events = iter(stream)
                    try:
                        first = next(events)
                    except StopIteration as exc:
                        raise UpstreamError(502, "upstream stream ended before its first event") from exc
                    route.update(provider="anthropic", model=body.get("model"), account=entry_label(entry))
                    return client, stream, events, first
                except Exception:
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass
                    try:
                        client.close()
                    except Exception:
                        pass
                    raise

            client, stream, events, first = with_rotation(open_stream, pool, model=body.get("model"))

            def send_event(payload):
                if payload.get("type") == "content_block_start":
                    restore_tool_names({"content": [payload.get("content_block")]}, names)
                data = f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n".encode()
                self.wfile.write(data)
                self.wfile.flush()

            try:
                self.close_connection = True
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("x-hermes-pool-route", json.dumps(route))
                self.send_header("connection", "close")
                self.end_headers()
                try:
                    send_event(first.model_dump(mode="json", exclude_unset=True))
                    for event in events:
                        send_event(event.model_dump(mode="json", exclude_unset=True))
                except (BrokenPipeError, ConnectionResetError):
                    pass  # client left; close upstream without another write
                except Exception as exc:
                    try:
                        send_event({"type": "error", "error": {"type": "api_error", "message": str(exc)[:2000]}})
                    except OSError:
                        pass
            finally:
                # Do not let cleanup errors reach do_POST's JSON error handler:
                # headers may already be committed to SSE.
                try:
                    stream.close()
                except Exception:
                    pass
                try:
                    client.close()
                except Exception:
                    pass

        def do_GET(self):
            if self.path.rstrip("/") in ("", "/health"):
                return self._send(200, {"ok": True})
            self._send(404, {"error": {"type": "not_found", "message": self.path}})

        def do_POST(self):
            route: dict = {}
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                path = self.path.split("?")[0].rstrip("/")
                if body.get("stream") and not path.endswith("/messages"):
                    raise ValueError("stream=true is supported only on /v1/messages; call this route without streaming")
                if path.endswith("/messages"):
                    resolve_model("anthropic", body.get("model"))
                    if body.get("stream"):
                        return self._stream_messages(body, pool_for("anthropic"), route)
                    out = anthropic_messages(body, pool=pool_for("anthropic"), route=route)
                elif path.endswith("/responses"):
                    resolve_model("openai-codex", body.get("model"))
                    out = codex_responses(body, pool=pool_for("openai-codex"), route=route)
                elif path.endswith("/embeddings"):
                    out = embeddings(body)
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
    a.add_argument("--json", action="store_true", help="print result metadata (including Anthropic stop_reason)")
    sub.add_parser("models", help="subscription models the pool serves")
    sub.add_parser("check", help="one ping per pool; exit 0 when both answer")
    s = sub.add_parser("serve", help="local Anthropic/OpenAI-compatible endpoint on the pool")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    e = sub.add_parser("env", help="print shell exports for authenticated local SDK clients (contains the token)")
    e.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)

    if args.cmd in ("env", "serve"):
        try:
            if args.cmd == "serve":
                serve(args.host, args.port)
            else:
                if not 1 <= args.port <= 65535:
                    raise ValueError("port must be between 1 and 65535")
                token = serve_token()
                base = f"http://127.0.0.1:{args.port}"
                for name, value in (("ANTHROPIC_BASE_URL", base), ("ANTHROPIC_API_KEY", token),
                                    ("OPENAI_BASE_URL", base + "/v1"), ("OPENAI_API_KEY", token)):
                    print(f"export {name}={shlex.quote(value)}")
        except (ValueError, OSError) as exc:
            print(f"hermes-pool-llm: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.cmd == "models":
        routes = managed_routes()
        if routes is None:
            routes = {key: [value] for key, value in DEFAULT_MODELS.items()}
        for (p, t), models in sorted(routes.items()):
            for m in models:
                print(f"{p:13} {t:9} {m}")
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
    if r.get("fallback_from"):
        print(f"hermes-pool-llm: {r['fallback_from']} refused (pool exhausted); served by {r['model']}",
              file=sys.stderr)
    print(json.dumps(r) if args.json else r["text"])
    stop_reason = r.get("stop_reason")
    if r["provider"] == "anthropic" and (
        stop_reason == "refusal" or (stop_reason == "max_tokens" and not r["text"].strip())
    ):
        print(f"hermes-pool-llm: completion failed (stop_reason={stop_reason})", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
