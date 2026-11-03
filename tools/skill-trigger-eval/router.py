"""Router run: one ask against the rendered skills index, recording the skill_view calls of the first reply.

Talks to an Anthropic-compatible ``POST /v1/messages`` (``hermes-pool-llm serve``), so the calls run on the
subscription pool. stdlib only.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

# A small, realistic tool belt: skill_view plus the everyday tools a Hermes chat has, so answering without a skill
# (or reaching for a tool first) is a real option and every loaded skill is a choice, not the only move available.
TOOLS = [
    {"name": "skill_view", "description": "Load a skill's SKILL.md, or one of its linked references, templates or scripts.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string", "description": "The skill name"},
                                                       "file_path": {"type": "string"}}, "required": ["name"]}},
    {"name": "terminal", "description": "Run shell commands on the user's machine.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read a text file with line numbers.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "search_files", "description": "Search file contents or find files by name.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},
    {"name": "web_search", "description": "Search the web.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "web_extract", "description": "Extract content from web page URLs.",
     "input_schema": {"type": "object", "properties": {"urls": {"type": "array", "items": {"type": "string"}}},
                      "required": ["urls"]}},
    {"name": "clarify", "description": "Ask the user a question when you need a decision.",
     "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
]


def build_body(system: str, ask: str, model: str, max_tokens: int = 1024) -> dict:
    return {
        "model": model, "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "tools": TOOLS, "messages": [{"role": "user", "content": ask}],
    }


def parse_reply(reply: dict) -> dict:
    """{'loaded': [skill names in call order], 'tools': [tool names], 'text': str} from a /v1/messages reply."""
    loaded, tools, text = [], [], []
    for block in reply.get("content") or []:
        if block.get("type") == "tool_use":
            tools.append(block.get("name"))
            inp = block.get("input") or {}
            if block.get("name") == "skill_view" and isinstance(inp.get("name"), str) and not inp.get("file_path"):
                if inp["name"] not in loaded:
                    loaded.append(inp["name"])
        elif block.get("type") == "text":
            text.append(block.get("text") or "")
    return {"loaded": loaded, "tools": tools, "text": "".join(text)[:400]}


def call(base_url: str, body: dict, timeout: int = 180, retries: int = 4) -> tuple[dict, str]:
    data = json.dumps(body).encode()
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(base_url.rstrip("/") + "/v1/messages", data=data, headers={
            "content-type": "application/json", "x-api-key": "pool", "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read()), resp.headers.get("x-hermes-pool-route") or ""
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            last = e
            body_txt = ""
            if isinstance(e, urllib.error.HTTPError):
                try:
                    body_txt = e.read().decode()[:300]
                except Exception:
                    pass
                if e.code == 400:
                    raise RuntimeError(f"bad request: {body_txt}") from e
            time.sleep(min(60, 5 * 2 ** attempt))
    raise RuntimeError(f"router call failed after {retries} attempts: {last}")


def run_ask(base_url: str, system: str, ask: str, model: str) -> dict:
    t = time.time()
    reply, route = call(base_url, build_body(system, ask, model))
    out = parse_reply(reply)
    usage = reply.get("usage") or {}
    out.update(route=route, secs=round(time.time() - t, 2), stop=reply.get("stop_reason"),
               input_tokens=sum(int(usage.get(k) or 0) for k in
                                ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")))
    return out
