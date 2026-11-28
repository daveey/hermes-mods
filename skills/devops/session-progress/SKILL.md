---
name: session-progress
description: "Use when asked if a session is moving forward or stalled."
version: 1.0.0
author: daveey
license: MIT
triggers:
  asks:
    - "is this session making progress?"
    - "which chats haven't moved in the last half hour?"
    - "predict what this session should show by the next check"
  not_for: "one named stuck pattern (broken-sessions); a card with many runs (kanban-card-churn)"
metadata:
  hermes:
    tags: [kanban, sessions, progress, stuck, predictions, session-repair]
    related_skills: [broken-sessions, kanban-card-churn, try-to-unblock, kanban-red-sweep]
---

# Session progress: is this chat moving forward?

The hub's deterministic `BS-no-progress` tick (`plugins/session-repair/progress.py`) checks chats every 15 min
and predicts evidence due in the next window. Update this skill with any rule change.

## Report progress against the requested outcome

Read current delivery state before reporting. Lead with what works, what remains unfinished, the concrete blocker
and who acts next—not routing receipts. Name a resumed run's authorized phase: "deployment verification is running"
is not "the migration is unstuck." Separate completed implementation, deployment, reconciliation and end-to-end
acceptance. Treat census counts as last-verified unless freshly measured. On repeated "what is happening?" questions,
replace renewal-by-renewal narration with the remaining acceptance and discriminating next deliverable. Cite the
underlying refusal/error receipt before explaining a restriction; a summary label alone does not explain its cause.

## What a session is

The chat's open cards: every card linked to it (`tasks.session_id`, or an unreleased `task_chat_links` row for this
gateway) plus the live cards on their dependency/awaiting chain (the session-panel feed's walk, the same set
`scan.py` judges). A chat with no open card but live background processes is judged by those processes.

## 1. Progress since the last snapshot (any one meets the session)

- A card reached done/archived, or changed status (including leaving the set), or a new card joined.
- A card emitted a progress event: any event except liveness/infrastructure ticks (`heartbeat` without a `note`,
  `external_watch`, `wake_check_*`, `wake_episode_*`, `dispatch_skipped`, `comment_delivered`, ...) and except
  session-repair's own comment or nudge wake. A heartbeat **with a note** is an agent progress report and counts.
- A new run id on a card; an awaited card changed status; a watcher's observation changed (merge-queue position, job
  counts).
- A new chat turn (not session-repair's own `[session-repair]` message).
- A background process's log grew, or a new process started.

## 2. Healthy waits (no progress yet, nothing due)

- `healthy_running`: a running card (or review with a running reviewer) with a progress event in the last 30 min.
- `healthy_wait`: a park on a producer whose prediction is not due: a CI/deploy run within park + `expected_s`
  (else 45 min) + 15 min; a merge queue within the window; an awaited card that is itself open and predicted; or a
  fresh core probe/watch receipt. A cardless chat whose processes' window is open.
- `clock_not_due`: a clock park with a reason and a future wake (due at wake_at + 10 min, not +30 min).
- `long_wait`: a CI/deploy/external/resource park past its deadline (or one awaiting a card this host can't read).
  That is BS-long-wait's call (its probe/watch receipts and nudge cadence), so it is never a BS-no-progress miss: on
  the first hub shadow pass every such "miss" was a park BS-long-wait had already judged healthy.
- `pending`: nothing healthy, but nothing due yet (a first window).

## 3. Acceptable terminal

`valid_red`: every open card is blocked on a **valid** `human_request` (core `needs_human`), sits in `triage` (a
decision queue its specifier / spend reviewer / supervisor owns), or is under a deliberate core hold (containment,
spend decision), and the rest only depend on those. A sticky block without a valid ask is not terminal.

## 4. Anything else: not moving

A prediction came due with no progress signal anywhere in the chat:
- `missed_once`: first miss on a card → the guarded nudge (owner comment + reassessment wake of that exact park).
- `missed_twice`: second consecutive miss on the same card (the miss re-arms a full window later, so ~30 min
  apart) → one fix card linked to the chat, deduped per card per 24 h.
- `process_only`: a cardless chat whose only waits are untracked background processes whose logs stopped → a
  resumed chat turn asking it to re-check; a second miss → a fix card.

Never a human ask unless the step truly needs David (`generate-unblock-card` for the ask's shape).

## 5. Predictions (what to expect by the next window)

Each tick writes `$HERMES_HOME/state/session-progress/<chat>.json`: per open card `{status, event_id (newest
progress event), run_id, park, awaited, prediction, misses}`, plus `predictions: [{card|process, expect, due_at}]`.

| state | expect | due |
|---|---|---|
| running | a progress event or status change | first seen + 30 min |
| ready | a worker claims it | + 30 min (+2 h with a recorded dispatcher skip) |
| park on CI/deploy | the run completes and the park wakes | park + expected_s (45 min) + 15 min |
| park on merge queue | position falls or it merges | + 30 min |
| park on a card | that card's own prediction | never, until it is done; then done + 10 min |
| clock park | the wake fires | wake_at + 10 min |
| other external / resource | the producer delivers | park + BS-long-wait threshold (3 h / 2 h) |
| todo | parents finish / promotion | dependent / parents done + 10 min |
| bare background process | its log grows | + 30 min |
| valid human_request, core hold | the person / owner acts | never |

A prediction keeps its deadline while the card's state is unchanged; any change restarts it.

## Judging one chat by hand

1. `python3 ~/.hermes/plugins/session-repair/scan.py --rule BS-no-progress --snapshots` (read-only) and read
   `progress.by_chat[<chat>]` and that chat's snapshot; or read the persisted
   `~/.hermes/state/session-progress/<chat>.json` and the tick's `ticks.jsonl` `progress` counts.
2. For each `missed_*`: `kanban_show` the card; check the newest events and what its prediction expected. A real
   producer the scanner cannot see (a live process not in the registry, an awaiting ref on another gateway) is a
   rule gap: add a fixture + test in `plugins/session-repair/tests/test_progress.py`, then fix `progress.py`.
3. A named stuck shape (overdue wake, awaited card done, stuck run) belongs to its `broken-sessions` pattern; when
   another rule flagged the card the same tick, `BS-no-progress` records the miss but files nothing.

## Rollout

Shadow first: `config.json` `shadow_rules` (default `["BS-no-progress"]`) keeps it log-only (`actions-shadow.jsonl`,
`outcome: rule_shadow`) whatever `mode`/`nudge_mode` say. After 24 h of adjudicated shadow rows set
`"shadow_rules": []`. Reverse by restoring the list.
