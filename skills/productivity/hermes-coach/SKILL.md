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
| `scripts/coach_feed.py` | hosts that push | pull → digest → commit `<host>/` → push, to a private git repo (`--exclude-profile` skips shared bots holding other people's chats) |
| `scripts/coach_mine.py` | the coach | KPIs per host + overall (this window vs previous), top-N unlabeled sessions by waste score; `--write-kpis` appends `reports/kpis.jsonl` |

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

## Running the coach Bot

A Bot profile on an always-on gateway owns the model work. Two crons: a no-agent feed for that host's own sessions
(silent unless broken, `--deliver local`), and a daily no-agent prerun that pulls, digests, runs
`coach_mine.py --write-kpis` and prints the labeling queue plus numbered instructions with `--deliver bot-chat`.
The Bot labels per `references/labeling.md`, writes `findings/` + `reports/`, then commits through a push helper.
Put the KPI goals and the user's learning track in its SOUL.md so every report names the next drill.

After changing a script: commit to hermes-mods (the Macs fast-forward through sync), then re-ship the changed files
to the cloud profile's `scripts/` and its skill copy, compare byte counts, and rerun the feed on every host so all
digests share one version (`DIGEST_V`).

## Pitfalls

- Verify redaction before the first push to a new repo: `gitleaks dir <repo>/<host>` must report no leaks.
- Hermes writes many user-role messages itself: delegation results, cross-session messages, turn-cap notices,
  `/goal` continuations, `[IMPORTANT: …]` notices, compression summaries, model-switch notes. Counted as user turns,
  they inflated repeat-fix about 10× and the Hermes-meta share about 4×. Extend `SYNTHETIC` whenever a new
  prefix appears (`grep -rhoE '"text": "\[[A-Z][A-Za-z: ]{4,40}' <host> | sort | uniq -c`), and normalize
  per-session rates by real user turns, not by the kept turn sample.
- Sanity-check a new KPI against the raw numbers before reporting it. Weight delegated work by spend: a count-based
  Kanban share (sessions vs orchestrating chats) reads 68% when the cost-weighted one is 14%, because each card is a
  session while one chat can hold 50 subagents.
- Treat the coach's first report as a check of the pipeline itself: it audits the digests and finds metric bugs.
  Fix those first, before acting on its usage findings.
- Keyword signals (`hermes_meta` etc.) hit incidental mentions. Classify a session by title keywords or by the
  signal's share of real turns, never by a raw count ≥ N.
- Exclude shared team-bot profiles (`--exclude-profile`): they hold other people's conversations.
- `[REDACTED]` inside ordinary prose usually means the `token|password …` rule caught a word. That rule requires
  a digit and ≥12 chars to keep false positives down.
- User messages can be JSON lists of multimodal parts. `flatten()` keeps the text and turns images into `[screenshot]`.
