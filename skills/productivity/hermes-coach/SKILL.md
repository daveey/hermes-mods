---
name: hermes-coach
description: "Use when setting up the cross-host session digest feed."
triggers:
  asks:
    - "add a host to the digest feed"
    - "which sessions were repeat-fix loops?"
    - "why is the coach feed failing?"
  not_for: "tuning detectors or coaching Hermes use (use hermes-training)"
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
| `scripts/session_digest.py` | every host | `state.db` (+ `profiles/*/state.db`) → `<out>/<host>/<profile>/<id>.json`, rewritten only on change; incremental via local `<out>/<host>/.digest-cursor.json` (unchanged sessions skip the transcript read; `--full`, auto-full every 7 d) |
| `scripts/coach_feed.py` | hosts that push | pull → digest → commit `<host>/` → push, to a private git repo (`--exclude-profile` skips shared bots holding other people's chats); first runs the self-heal machine-signal forwarder (`plugins/coach/signals.py forward`, ssh to the hub, no-op without `hub_ssh` in `coach/selfheal-config.json`); also writes `<host>/selfheal-seen.json` (this host's detections, ids + times only) so the hub's ask-gate counts another host's shadow/live detection as "Detected, not a miss" |
| `scripts/coach_mine.py` | the coach | KPIs per host + overall (this window vs previous; interactive = allow list `INTERACTIVE_SOURCES` desktop/tui/cli/slack/telegram/discord/whatsapp/signal with a user turn, so bot sources like `bb-reviewer`/`taxprep-*` never dilute p50/p90; those print on an `other_sources:` line with counts + cost and land in the `kpis.jsonl` row), top-N unlabeled sessions by waste score; digests matching `DIR/exclude-profiles.txt` / `--exclude-profile` `HOST/PROFILE_GLOB` (shared team bots) are dropped at load; `--write-kpis` writes today's row of `reports/kpis.jsonl` (a rerun replaces it); `--write-adoption` evaluates the coach plugin's curriculum `adopted` tests (public file + `curriculum/*.yaml` overlays) into sticky `reports/adoption.json` (needs PyYAML or ruamel.yaml) |
| `scripts/coach_weekly.py` | the coach | weekly context: the 6 plan KPIs (latest `kpis.jsonl` row vs the row a week earlier, gaps recomputed from digests), top findings by est. waste, spend vs output per agent (host/profile × cron job / kanban / chats / source: $ vs the previous window, no-op share from `[SILENT]`/"nothing to fix" last replies, flags for mostly-no-op agents ≥ $5, crons repeated across hosts, doubled spend), `--check key=value@UTC#signal` before/after verdicts (keep / raise / revert), the self-heal lane (filed / proposed / approved / fixed / reverted / not-a-defect, recurrence, $ per fix from `selfheal_*` coach events + `selfheal:<component>-<hash>` cards, or the hub's `reports/selfheal-lane.jsonl` written by `--selfheal-export`; "no activity" when there is none), proposal state + rejected list |
| `scripts/coach_yield.py` | the coach | read when reviewing Engine D source yield or spend: read-only six-week source aggregation, seven UTC-day budget buckets, and candidate source-list replacement blocks; does not apply edits |
| `scripts/coach_proposals.py` | both | `add` parses ```` ```coach-proposal ```` JSON blocks from a report into the append-only `reports/proposals.jsonl` (drops anything matching a rejected fingerprint) and assigns the rollout `tier` (auto / canary / review from kind+target+cost+risk; a block's `tier` may only raise it); `list`, `rejected`, `mark`, `outcomes` (bounded Last outcomes / Tried / Watch block from the metadata-only `card_outcome` rows `coach-apply auto` records for done/archived linked cards on its explicit board; never changes `status`; also in the weekly context). Tests: `test_coach_outcomes.py` |
| `scripts/coach_apply.py` | the user's machine | `coach-apply` engine: show/apply/reject/revert, and `auto` = the daily rollout pass (auto tier: gate then apply; canary: this host only + a before/after `check`, promote on KEEP, revert on REVERT; review: a Kanban card; `kind: fix`: the fixer lane; pending > 7 days with a failing gate: dropped with the reason). Tests: `test_coach_apply.py` |
| `scripts/coach_fixer.py` | the user's machine | fixer lane: `fix` proposals (pattern slug, ≥2 sessions, hypothesis, acceptance) → one `coach-fix:<slug>` card a day (`--max-runtime 3600`, created_by coach) while fixer spend today < $7; spend = coach cards' worker-session cost per UTC day, logged as lane `fixer` in `research/state/spend.jsonl`. `spend` / `gate` CLI. Tests: `test_coach_fixer.py` |
| `scripts/coach_eval.py` | the user's machine | replay eval that gates a change before rollout. `detector`: a threshold / judge-prompt (`COACH_DETECT_TAG` detect file) / prefilter-module variant scored against the labeled reply-check and fix-loop sets (`~/.hermes/coach/{replyset,fixloop}`); score = F0.5 when precision ≥ 0.75, else 0; free. `behavior`: the frozen benchmark (`<data>/eval/bench-v1.jsonl`, 36 real episodes: handoff, confirm, unverified_done, repeat_fix, unwatched, control; `bench-build` mines a new version) replayed under baseline vs candidate instructions (AGENTS/SOUL/skill/memory edits applied only in a private eval home `~/.cache/coach-eval/root`, layered into the system prompt like Hermes does) with one bounded provider request per call (`coach_llm_worker.py` under the Hermes interpreter: Hermes credentials + price table, no tools/retries, explicit max_tokens), graded blind by an LLM judge; PASS needs a target gain ≥ max(2, n/4) (run-to-run noise is ±2 of 6), `--pool` / `verdict` combine repeated runs; hard cap: each call's worst case (bytes-bound input + max_tokens × price table) is reserved in `spend.jsonl` lane `trial` before dispatch and settled to the measured cost after, so `--budget` (default $5) and the $10/day lane can't be exceeded. Exit 0 PASS / 1 REJECT / 2 INCOMPLETE. Tests: `test_coach_eval.py` |
| `scripts/coach_trial.py` | the user's machine | trial lane: the weekly digest's one `trial` proposal (idea, ≥2 gap findings, pattern, candidate) → one goal-mode `coach-trial:<slug>` card a day (2 h) while the lane's spend (trial cards + eval runs) today < $10; the card builds the idea behind a flag, runs `coach_eval.py`, attaches the report, and on PASS registers the real proposal with its tier |
| `scripts/coach_askgate.py` | the always-on hub (cron `coach-kpi-weekly`, Mon 15:30 UTC, via a wrapper that also pushes `reports/askgate/` + `reports/selfheal-lane.jsonl`) | two weekly KPIs (Mon-Sun PT) from the cross-host digests, each hit with session + date + quote, target 0: `ask_me` (David's "why are you asking me / I already did it / do it yourself") and `had_to_ask_fix` (David asking for a Hermes defect to be fixed or root-caused: "make a card to fix", "root cause", "figure out why", "broken again", "why did you not…", screenshot + "fix"; interactive chats only, ask-me wins a tie) and `had_to_ask_fix_bb` (the same for Baby Bear product
bugs, by the turn's words (Desktop attachment paths stripped), then the title, then BB vs Hermes word counts over the session's turns + replies: appended to bb-tuner's `work/askgate-bb-misses.jsonl` (`--bb-handoff`),
which its pre-run shows until bb-tuner closes each with the detection BB's own monitoring lacked; never sent to the
self-heal lane, whose strict judge scopes product bugs out); `--weeks 6 --baseline FILE`, `--last-week --post --selfheal` (never an Inbox card: the report is status. Hits no lane took become ONE Kanban review card, idempotency key `ask-gate-weekly:<monday>`, assignee default; none = no card. `deliver()`, tests `Deliver`; writes the marker `reports/askgate/<monday>.json`, and a host that finds another host's marker skips its post). With `--selfheal` every hit the self-heal lane has no filing for goes to it as a monitor finding (`signals.py`), and the card keeps its Do-it review prompt only for hits the lane did not take (all of them in shadow). A turn whose `prev` is an Inbox notice (footer "Press **Do it** to run the proposal") and whose text is that notice's `**Proposed:**` text is a Do-it press, not David: `collect()` skips it (only words he added after the proposal are classified), lists it as "Inbox Do-it presses (not counted)" and `--json` weeks carry `inbox_doit`; the drain's `still_flags` withdraws queued rows for such turns by reading the session digest (`$HERMES_COACH_DATA`, default `~/code/hermes-coach-data`). Tests: `test_coach_askgate.py`, `test_askgate_provenance.py` (`InboxDoit`) |
| `scripts/coach_report_only.py` | the coach (via `coach_weekly.py`'s `section()`) | weekly `report_only` rate (final reply diagnoses an agent-fixable problem and neither fixes, starts nor proposes a fix) per model family and trigger (human-question <= 10%, event <= 20%); judge calls go through `coach_eval`'s bounded runner and the engine budget. Read when that KPI moves or its rubric is questioned. Tests: `test_coach_report_only.py` (offline stub judge) |
| `scripts/coach_model_bench.py` | the user's machine (hermes-sync model gate) | bench-v3: replays real report-only turns (tool calls + clipped results, real tool schemas) and control episodes for ONE model, graded by the report-only rubric with Wilson intervals; `build` from the eval set + `state.db`, `run --model M --budget N` (paid model calls under `coach_eval` Budget; the bench holds real chat text, keep it in the private data repo). Use before switching a default model. A model outside the subscription routes runs through OpenRouter: `COACH_EVAL_PROVIDER=openrouter token-broker exec --scope openrouter.inference -- coach_model_bench.py run --model <openrouter id> ...` (or `COACH_EVAL_PROVIDER=nous` with `--scope user/nous`: the Nous portal serves the same catalog and ids, for when the OpenRouter key is at its spend limit) (Anthropic-compatible Messages wire, prices from OpenRouter's model list; judge and simulator stay on subscription). Pick comparison baselines by size: benchmark a new model against open models of matching total/active parameters (e.g. a 40B/2.5B-active MoE against Qwen 35B-A3B / 30B-A3B, or Kimi when sized alike) on the same bench file and the same `--max-tokens`, with frontier reports (Opus, GPT) only as a labelled reference row, never the comparison: a small model judged only against frontier models answers the wrong question. A heavy-reasoning model can spend the whole output cap thinking: the report counts those replies as cut by the cap, and `--max-tokens N` raises it. Tests: `test_coach_model_bench.py` (offline) |
| `scripts/engine_budget.py` | the user's machine (+ gates everywhere) | one hard $20/UTC-day cap over every lane (scout $3, fixer $7, trial $10, `bot` = the coach Bot's own turns), plus lane `selfheal` ($200/UTC day, its own cap outside the $20 total; cards keyed `selfheal:<fp>…`, see `plugins/coach/README.md` "Self-heal lane"): `snapshot()` sums the ledger + this host's live coach-card cost; every gate (`coach_fixer.gate`, `coach_trial.gate`, `coach_eval.Budget`, the scout's `x_budget_left`) refuses at its lane cap OR the total. `watchdog` (each `coach-apply auto` + launchd every 15 min): a RUNNING engine card over its card budget (`Budget [for this card] ≤ $N` in the body, else the lane cap) is blocked needs_input (no auto-resume: raise its Budget line or close it), one stopped on its lane/total cap is scheduled to the next UTC midnight; either way its worker is SIGTERMed; never a non-engine card; stops logged to `research/state/budget-stops.jsonl` (weekly digest lists them), Inbox `engine-budget:<day>` with a raise-the-cap Do-it (`budget-overrides.jsonl`); cap stops resumed the next UTC day. Tests: `test_engine_budget.py` |

The private repo layout is `<host>/<profile>/<session_id>.json`. Each host writes only its own subtree, so pushes never conflict.

Digest = session stats (msgs counted from `messages`, since `sessions.message_count` goes stale), cost,
subagent children folded into the root, user turns (first 10 + last 50, ≤500 chars; each with `prev`, the agent reply before it clipped to its
first 150 + last 450 chars, v6, for the self-heal backstop judge; plus `prev_interrupt` `{reason, waited_s, prompt_tokens}`
when that reply was a model wait core recorded as interrupted), tool counts, tool-error
counts, pattern signals, `features` (content-free feature-use counts from tool-call args: kanban_create options,
todo, cron/profile/board creation, worktrees; v4), and the last reply (≤800 chars). Removed: injected blocks (`<memory-context>`,
system notes, pasted content), Hermes-written user-role messages (background-process notices, compression
summaries, which are counted as `synthetic_*` signals), base64 image parts, and secrets (provider keys, GitHub/Slack
tokens, JWTs, private keys, `token=…` values with digits, long opaque blobs). Home paths become `~/`.
Never included: tool output, system prompts, reasoning.

## Eval publication

`coach_eval.py` report commands and `coach_model_bench.py run` automatically commit changed
`research/state/spend.jsonl` and in-repo `--out` reports (Markdown, JSON, and bench replay cache)
to the `COACH_DATA` checkout, then push with union-JSONL/rebase retries. Use `--no-publish`
to leave them local. Publication errors go to stderr without changing the evaluation exit code;
check that diagnostic as well as the verdict. Files outside the checkout are never published.
Tests use local bare Git remotes and offline model stubs (`scripts/test_coach_publish.py`).

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
target, change, evidence, title, short, effect, before, risk, tier, signal}`; `short`/`effect`/`before` are the Inbox
card copy, see the `coach_proposals.py` docstring), registers them with `coach_proposals.py add`, and replies with the summary.
On the user's machine a daily `coach_apply.py auto` (launchd) rolls them out by tier, editing through the normal mod
path (config set / shared config / skill file in the mods repo, committed) and appending each decision with its revert
info; the next digest lists every apply with `coach-apply revert <id>`. The user can still reject or revert by hand.
The user wants every accepted improvement in the Hermes Inbox: `notify_inbox` in `coach_apply.py` posts one card per
proposal (`hermes inbox post --key coach-improvement:<id>`, so canary → kept/reverted updates the same card) from every
path that changes status: auto, canary verdicts, hand apply/revert, and `coach_proposals.py mark` (review-tier cards
mark there, bypassing coach-apply). A new status-changing path must call it too. `coach-apply inbox --days N` backfills.
Card layout ("Now / Before", board `~/design/coach-inbox-cards/`): row title `<Tip|Rule|Code|Setting>: <short>` with a
`Trying · ` / `Kept · ` / `Undone · ` prefix only off the plain applied state; body = **Now** (effect) + **Before**
(the problem), then the literal change (the tip as a `[!TIP]` alert, the rule text added, `key: old → new`, or what
a commit shipped and left out), then one muted line (scope · live time PT · `@session` source · undo · id). The Do-it
prompt goes out with `--prompt-label`, so the card shows one line instead of the whole review prompt. A proposal
without `effect`/`before` falls back to the evidence quote; append a field-only line to `proposals.jsonl` (`{"id",
"short", "effect", "before"}`, no `status`/`at`) to fix the copy of an old one.
The cloud Bot can't read Mac paths: ship any doc that SOUL.md points at (e.g. the plan) into the profile's `work/`
and reference it by that cloud path. Re-ship whenever the source changes.

After changing a script: commit to hermes-mods (the Macs fast-forward through sync), then re-ship the changed files
to nous-1 `/opt/data/profiles/coach/scripts/` (skills there sync from git on their own; `nous-push` ships plugins only).
Recipe: one local script per ship, each file as `base64 -d > <name>.new <<'B64' … B64`, then print `wc -c` and
`diff <name> <name>.new | grep -c '^[<>]'`; run it with the gateways skill's `nx.sh`; if the diff is only your change,
`mv` the `.new` files in a second nx.sh call and rerun the script against `/opt/data/profiles/coach/work/hermes-coach-data`.
A new `coach_weekly.py` section is ignored by the Bot until `coach_sunday.py` (`~/code/hermes/hosts/nous-1/coach/`,
commit to `state`) gets a matching numbered instruction: edit and re-ship both. A digest change also needs every
host's feed rerun so all digests share one version (`DIGEST_V`).
The cloud profile has no `coach_apply.py`: guard any import of it from a shipped script with `try/except ImportError`,
or that path crashes there. To re-render an OPEN Inbox card in a new format, `hermes inbox archive <sid>` then post
again (a same-key re-post onto an open card appends an update under the old message); rename the archived copy first,
or the new card's title gets a ` (2)` suffix.

## Inbox card types

Self-heal never asks the user: the lane auto-approves every investigation's proposal (`auto_approve`, default on, in
`coach/selfheal-config.json`) and the only self-heal card in the Inbox is the fix card's "Fixed: …" result. Any new
self-heal path or monitor must keep that: no Do-it proposal cards; open legacy `selfheal` cards are resolved with
`hermes inbox act <sid> do_it` on the hub, then `self_heal.py sync` files the fixes. Lane admission counts only
*running* cards, so a bulk approval admits every fix at once past the 3-running / $200 caps; say so when you do one.

Improvement cards are `coach-improvement` typed (`inbox-types/coach-improvement.yaml`, fields `id`): **Review** (the
check-whether-it-helps prompt), **Keep** (resolve; a canary is promoted at once), **Undo** (`task`: runs
`coach-apply revert`), **Not now** (resolve; nothing of that kind + target is proposed for 14 days), **Never**
(resolve; never proposed again, and undone if live). `coach-apply auto` begins with `sync_inbox`: it reads each recent
card's `hermes inbox outcome coach-improvement:<id>` and acts once per outcome, logging it to
`reports/inbox-decisions.jsonl`; Not now / Never are rows in `reports/suppressions.jsonl` (`coach_proposals.suppressed`),
which `coach_proposals.py add` (the cloud Bot too, once it pulls the data repo) and the auto pass honour. A pending
proposal that is suppressed is dropped with the reason. `coach_apply.py decide <id> keep|not_now|never` does the same
from a chat (Review closes the card's buttons); `coach_proposals.py suppressions [--clear KIND TARGET]` lists or lifts
them. A failed revert on Never is logged in `inbox-decisions.jsonl` (detail `FAILED: …`) and not retried. `engine-budget` cards (field `day`): **Raise cap…** (`task`,
input = the new total in USD; the card's agent writes `budget-overrides.jsonl` and unblocks the stopped cards) and
**Leave paused**; the watchdog does not re-post a card for a day whose card got Leave paused (after Raise cap it posts again if the new cap is hit). An inbox plugin
without card types, or a host without these type files, gets the old plain card (the post retries without `--type`).
Undone notices are always plain cards.

## Done means

- [ ] Before classifying an `ask-gate-weekly` hit as a detector miss, did you inspect exact historical log events matching its quote, session and incident scope, allowing live detection without a retained lane owner and recording missing evidence as unknown rather than a miss?
- [ ] For each frozen weekly ask-gate candidate, did you inspect raw envelopes and earlier product replies before deciding human authorship, rather than treating API ingestion metadata or session source as proof?
- [ ] Before calling an ask-gate hit a miss, checked every `<data>/*/selfheal-seen.json` plus local `coach/selfheal.json` with `detection_for`, and, when testing the cross-host path on the hub, removed same-session hub fingerprints only from a scratch home copy so local detections cannot mask the cross-host result?

## Pitfalls

Read `references/pitfalls.md` before acting on this skill's steps (moved verbatim from SKILL.md).

