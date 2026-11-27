# Frozen administrative deadlines for parks, links, landings and review queues

**Scope:** Deadline stamping and census rules for escalation_deadline_missing; remote probe rehome.

**Source ordinals:** 6414, 6415, 6416, 6453, 6463, 6479, 6483, 6499, 6516, 6517, 6518, 6578

## Behavior

- A running worker linking a reviewer then parking gets a frozen deadline on the link; an explicit episode keeps precedence over a later running link; a todo-stamped link on a running card is not a handoff.
- Wake, lane admission, Board-applied spend-review landings and release-to-todo landings freeze the dependency-wait deadline; ordinary triage dependency edges (including lane-merged triage cards) are covered while lane-owned clocks keep precedence.
- A park's deadline is its own clock and survives edge changes.
- The census exempts a card queued for its reviewer only when board config establishes that review dispatch is on; backend outage keeps the flag.
- Silent remote probes are rehomed on automatic repark.

## How it works

- `kanban_actionability_creation.py` (writers), `_selection.py` and `_sources.py` (source choice), `kanban_actionability.py` (census), `kanban_lanes.py`, `kanban_no_progress.py`.

## Code

Files touched by the series (paths relative to the source tree):

- `hermes_cli/kanban_actionability.py`
- `hermes_cli/kanban_actionability_creation.py`
- `hermes_cli/kanban_actionability_selection.py`
- `hermes_cli/kanban_actionability_sources.py`
- `hermes_cli/kanban_lanes.py`
- `hermes_cli/kanban_no_progress.py`
- `hermes_cli/kanban_spend_caused_review.py`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/test_kanban_actionability_landing_sources.py`
- `tests/hermes_cli/test_kanban_actionability_review_queue.py`
- `tests/hermes_cli/test_kanban_actionability_runnable_deadlines.py`
- `tests/hermes_cli/test_kanban_actionability_running_link.py`
- `tests/hermes_cli/test_kanban_no_progress.py`
- `tests/hermes_cli/test_kanban_parked_link_deadline.py`
- `tests/hermes_cli/test_kanban_release_landing_deadline.py`
- `tests/hermes_cli/test_kanban_spend_review_landing.py`
- `tests/hermes_cli/test_kanban_triage_link_deadline.py`
- `tests/hermes_cli/test_kanban_wake_check_home.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6414-6578, 12 commits: 6414, 6415, 6416, 6453, 6463, 6479, 6483, 6499, 6516, 6517, 6518, 6578) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Never read a todo edge as a running handoff.
- Failure to read dispatch config must be treated as not established.
- The release-to-todo landing consumes only the additive canonical Board receipt landing (exact todo status, sorted unique parents, no self edge, no unknown fields); legacy, ready or malformed receipts infer no landing, and receipt settlement alone does not release scope.
- No backfill, sweep, blanket TTL or exemption is added; the review-queue exemption requires review dispatch to be exactly True.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

The census and writers are core store logic.
