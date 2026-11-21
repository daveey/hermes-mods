# Kanban: preserve review custody across admission retry parks

Mirror entries 5218-5222.

## Behavior

- Review admission exhaustion recorded as `gave_up` made timed scheduling forget the review phase, so a wake escalated to a deep implementer and the no-code re-handoff consumed another independent reviewer. Now the resumable phase is preserved in schedule/unblock; typed admission refusals (and narrow legacy equivalents) are excluded from implementation escalation; original author custody is retained for repeated immutable merged PR/head/merge identities. Unknown or changed artifacts and intervening executed work still count as author evidence.
- 5219: keeps existing lifecycle contracts: only resumed review phases carry the additive `resume_status`; triage resumption and claimed review runs are covered, and an unclaimed review card cannot schedule.
- 5220: explicit manual promotion and continuation phase receipts supersede the admission review phase.
- 5221: a current ready source is authoritative when parking (sweep and reopen return implementation custody despite older review events); history recovery stays for parked/triage/parent-gated sources.
- 5222 is test-only (mutants: manual-promotion phase, older real failure behind an admission gave_up, changed handoff between matching ones).

## How it works

- `hermes_cli/kanban_review_retry.py`, `kanban_db_schedule.py`, `kanban_db.py`, `kanban_review_model.py`, `kanban_model_tier.py`.

## Code

- `hermes_cli/kanban_review_retry.py`
- `hermes_cli/kanban_db_schedule.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_review_model.py`
- `hermes_cli/kanban_model_tier.py`

## Tests

Source tests (not executed in this mirror pass): `tests/hermes_cli/test_kanban_review_retry_custody.py` (immutable merged re-handoff does not consume the reviewer, changed or unknown artifact keeps author, repeated unknown identity, intervening work, three refusals park and wake stay review, review admission gave_up does not escalate, worker gave_up still escalates, schedule/unblock preserves source lane, manual promotion supersedes, ready park does not reuse old review phase, older failure survives newer admission gave_up, changed handoff resets identity).

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- No blanket `review_requested` exemption; custody evidence must be immutable.

## Why core instead of a plugin

Scheduling, parking and tier escalation are core kanban lifecycle code.
