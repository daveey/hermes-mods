#!/usr/bin/env python3
"""Probe Anthropic credential-pool tokens across models and report a 429 matrix.

Reads ~/.hermes/auth.json (credential_pool.anthropic), probes each token with a
1-token request per model, and prints which model x account combinations work.

Usage:
  python3 probe_anthropic_pool.py [model ...]   # default: probe all visible models

A 404 means auth passed (bad model name); 429 is a genuine rate limit.
"""
import json, sys, time, urllib.request
from pathlib import Path

AUTH = Path.home() / ".hermes" / "auth.json"
API = "https://api.anthropic.com/v1"


# Max-plan OAuth tokens only serve requests shaped like Claude Code: this system prompt
# plus the oauth/claude-code betas. Without them Anthropic answers 429 even on a healthy
# account, which looks exactly like a rate limit. Never drop these.
CC_SYSTEM = [{"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."}]
CC_HEADERS = {
    "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
    "user-agent": "claude-code/2.1.293 (external, cli)",
    "x-app": "cli",
}


def call(tok, path, payload=None):
    if payload is not None:
        payload = {"system": CC_SYSTEM, **payload}
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(API + path, data=data, headers={
        "Authorization": f"Bearer {tok}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
        **CC_HEADERS,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as ex:
        return ex.code, None


def main():
    pool = json.loads(AUTH.read_text())["credential_pool"]["anthropic"]
    if not pool:
        sys.exit("no anthropic credentials in pool")

    # any token can list models; a non-200 here means the pool itself is broken
    status, models_resp = call(pool[0]["access_token"], "/models")
    if status != 200:
        sys.exit(f"model listing failed: HTTP {status} (auth problem, not a rate limit)")
    visible = [m["id"] for m in models_resp["data"]]
    models = sys.argv[1:] or visible
    unknown = [m for m in models if m not in visible]
    if unknown:
        sys.exit(f"unknown model(s) {unknown}; visible: {visible}")

    print(f"{'model':<28}" + "".join(f"{e['label'][:15]:<16}" for e in pool))
    ok = 0
    for m in models:
        row = ""
        for e in pool:
            st, _ = call(e["access_token"], "/messages",
                         {"model": m, "max_tokens": 8,
                          "messages": [{"role": "user", "content": "hi"}]})
            ok += st == 200
            row += f"{'OK' if st == 200 else st:<16}"
            time.sleep(0.4)
        print(f"{m:<28}" + row)
    if ok == 0:
        print("\nWARNING: zero successes. A uniform all-429 matrix usually means the probe is wrong, not "
              "that every account is limited. Check against a live client (e.g. a working Hermes or "
              "desktop session) before concluding anything.")


if __name__ == "__main__":
    main()
