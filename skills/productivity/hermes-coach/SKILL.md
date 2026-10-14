---
name: hermes-coach
description: "Use when mining Hermes sessions across hosts for usage patterns. Digests, feed, coach Bot."
version: 0.1.0
author: daveey
license: MIT
metadata:
  hermes:
    tags: [hermes, coaching, usage-analysis, sessions, power-user]
    related_skills: [hermes-training, ambitious-requests]
---

# Hermes coach: cross-host session mining

Collects a compact, redacted digest of every Hermes session on every host and feeds it to a coach Bot,
which finds anti-patterns and proposes improvements. Phase 1 of a measure → detect → nudge → adapt loop.

## When to Use

- Setting up or debugging the digest feed on a host, or adding a new host.
- Asking the coach-bot questions about usage across all machines ("which sessions were repeat-fix loops?").

## Pieces

| Script | Runs on | Does |
|---|---|---|
| `scripts/session_digest.py` | every host | `state.db` (+ `profiles/*/state.db`) → `<out>/<host>/<profile>/<id>.json`, rewritten only on change |
| `scripts/coach_feed.py` | hosts that push | pull → digest → commit `<host>/` → push, to a private git repo |

The private repo layout is `<host>/<profile>/<session_id>.json`. Each host writes only its own subtree, so pushes never conflict.

Digest = session stats (msgs counted from `messages`, since `sessions.message_count` goes stale), cost,
subagent children folded into the root, user turns (first 10 + last 50, ≤500 chars), tool counts, tool-error
counts, pattern signals, and the last reply (≤800 chars). Removed: injected blocks (`<memory-context>`,
system notes, pasted content), Hermes-written user-role messages (background-process notices, compression
summaries, which are counted as `synthetic_*` signals), base64 image parts, and secrets (provider keys, GitHub/Slack
tokens, JWTs, private keys, `token=…` values with digits, long opaque blobs). Home paths become `~/`.
Never included: tool output, system prompts, reasoning.

## Setup on a host

```bash
git clone git@github.com:<you>/hermes-coach-data ~/code/hermes-coach-data
/usr/bin/python3 <skill>/scripts/coach_feed.py --host <name>        # first run backfills 45 days
```

Schedule it every 30 min (launchd `StartInterval 1800` on macOS). Check `git log` in the data repo.
A host with a non-default home passes `--home` (e.g. `~/.bb-hermes`). Profiles are discovered automatically.

## Pitfalls

- Verify redaction before the first push to a new repo: `gitleaks dir <repo>/<host>` must report no leaks.
- `[REDACTED]` inside ordinary prose usually means the `token|password …` rule caught a word. That rule requires
  a digit and ≥12 chars to keep false positives down.
- User messages can be JSON lists of multimodal parts. `flatten()` keeps the text and turns images into `[screenshot]`.
