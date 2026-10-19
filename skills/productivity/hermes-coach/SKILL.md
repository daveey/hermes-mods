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
| `scripts/coach_mine.py` | the coach | KPIs per host + overall (this window vs previous), top-N unlabeled sessions by waste score; `--write-kpis` writes today's row of `reports/kpis.jsonl` (a rerun replaces it); `--write-adoption` evaluates the coach plugin's curriculum `adopted` tests (public file + `curriculum/*.yaml` overlays) into sticky `reports/adoption.json` (needs PyYAML or ruamel.yaml) |
| `scripts/coach_weekly.py` | the coach | weekly context: the 6 plan KPIs (latest `kpis.jsonl` row vs the row a week earlier, gaps recomputed from digests), top findings by est. waste, `--check key=value@UTC#signal` before/after verdicts (keep / raise / revert), proposal state + rejected list |
| `scripts/coach_proposals.py` | both | `add` parses ```` ```coach-proposal ```` JSON blocks from a report into the append-only `reports/proposals.jsonl` (drops anything matching a rejected fingerprint) and assigns the rollout `tier` (auto / canary / review from kind+target+cost+risk; a block's `tier` may only raise it); `list`, `rejected`, `mark` |
| `scripts/coach_apply.py` | the user's machine | `coach-apply` engine: show/apply/reject/revert, and `auto` = the daily rollout pass (auto tier: gate then apply; canary: this host only + a before/after `check`, promote on KEEP, revert on REVERT; review: a Kanban card; `kind: fix`: the fixer lane; pending > 7 days with a failing gate: dropped with the reason). Tests: `test_coach_apply.py` |
| `scripts/coach_fixer.py` | the user's machine | fixer lane: `fix` proposals (pattern slug, ≥2 sessions, hypothesis, acceptance) → one `coach-fix:<slug>` card a day (`--max-runtime 3600`, created_by coach) while fixer spend today < $7; spend = coach cards' worker-session cost per UTC day, logged as lane `fixer` in `research/state/spend.jsonl`. `spend` / `gate` CLI. Tests: `test_coach_fixer.py` |
| `scripts/coach_eval.py` | the user's machine | replay eval that gates a change before rollout. `detector`: a threshold / judge-prompt (`COACH_DETECT_TAG` detect file) / prefilter-module variant scored against the labeled reply-check and fix-loop sets (`~/.hermes/coach/{replyset,fixloop}`); score = F0.5 when precision ≥ 0.75, else 0; free. `behavior`: the frozen benchmark (`<data>/eval/bench-v1.jsonl`, 36 real episodes: handoff, confirm, unverified_done, repeat_fix, unwatched, control; `bench-build` mines a new version) replayed with `hermes -z` under baseline vs candidate instructions (AGENTS/SOUL/skill/memory edits applied only in a private eval home `~/.hermes/coach/eval/root`), graded blind by an LLM judge; PASS needs a target gain ≥ max(2, n/4) (run-to-run noise is ±2 of 6), `--pool` / `verdict` combine repeated runs; ≤ $5/run, spend logged as lane `trial`. Exit 0 PASS / 1 REJECT / 2 INCOMPLETE. Tests: `test_coach_eval.py` |
| `scripts/coach_trial.py` | the user's machine | trial lane: the weekly digest's one `trial` proposal (idea, ≥2 gap findings, pattern, candidate) → one goal-mode `coach-trial:<slug>` card a day (2 h) while the lane's spend (trial cards + eval runs) today < $10; the card builds the idea behind a flag, runs `coach_eval.py`, attaches the report, and on PASS registers the real proposal with its tier |

The private repo layout is `<host>/<profile>/<session_id>.json`. Each host writes only its own subtree, so pushes never conflict.

Digest = session stats (msgs counted from `messages`, since `sessions.message_count` goes stale), cost,
subagent children folded into the root, user turns (first 10 + last 50, ≤500 chars), tool counts, tool-error
counts, pattern signals, `features` (content-free feature-use counts from tool-call args: kanban_create options,
todo, cron/profile/board creation, worktrees; v4), and the last reply (≤800 chars). Removed: injected blocks (`<memory-context>`,
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
A weekly cron (Sunday, after the daily mine) prints `coach_weekly.py` output with instructions; the Bot writes
`reports/weekly-<date>.md` with 1-3 `coach-proposal` blocks (`{kind: skill_patch|config|shared_config|curriculum|plugin_rule|core,
target, change, evidence, title, risk, tier, signal}`), registers them with `coach_proposals.py add`, and replies with the summary.
On the user's machine a daily `coach_apply.py auto` (launchd) rolls them out by tier, editing through the normal mod
path (config set / shared config / skill file in the mods repo, committed) and appending each decision with its revert
info; the next digest lists every apply with `coach-apply revert <id>`. The user can still reject or revert by hand.
The cloud Bot can't read Mac paths: ship any doc that SOUL.md points at (e.g. the plan) into the profile's `work/`
and reference it by that cloud path. Re-ship whenever the source changes.

After changing a script: commit to hermes-mods (the Macs fast-forward through sync), then re-ship the changed files
to the cloud profile's `scripts/` and its skill copy, compare byte counts, and rerun the feed on every host so all
digests share one version (`DIGEST_V`).

## Pitfalls

- xAI `x_search` via the Responses API (outside research scout): send `"tool_choice": "required"` and use
  `grok-4.5`, or the model often answers `[]` from memory without searching (`usage.num_server_side_tools_used: 0`).
  Each call costs ~$0.4–0.7 (`usage.cost_in_usd_ticks` / 1e10 = USD), so log it and cap per day. Citations come back
  empty; the post URLs are in the answer text. Check them with `https://publish.twitter.com/oembed?url=<post>`
  (in testing 21/21 resolved).

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
- A new curriculum `adopted` test that needs a fact the digest lacks: add a counter to `call_features()` /
  `CMD_FEATURES` in `session_digest.py` (names only, never argument text), bump `DIGEST_V`, re-ship, rerun every feed.
  Sanity-check new adoption counts against real sessions before trusting them (agent-run commands count too).
- Exclude shared team-bot profiles (`--exclude-profile`): they hold other people's conversations.
- `[REDACTED]` inside ordinary prose usually means the `token|password …` rule caught a word. That rule requires
  a digit and ≥12 chars to keep false positives down.
- User messages can be JSON lists of multimodal parts. `flatten()` keeps the text and turns images into `[screenshot]`.
