# Responsive owner admission and unbound-submitter release

Patch: **5949**. Keeps the Kanban owner answering RPCs during admission, and lets a dead never-bound submitter's claim be released.

## Behavior

Dispatcher admission (`run_task`) builds the card policy off the owner event loop, so a cold or contended board read cannot starve receipt and control RPCs from other submitters. Admission no longer mounts a transcript-viewer resume: create/restore already mounted the route, and only submit and receipt polling are needed. A retried admission after a lost reply returns the original session and admission receipt with a single policy build.

Recovery: when a never-bound attempt's submitter died after being refused (exit code for owner-unavailable) the attempt is treated as provably over even if the owner is alive and the admission is queued, absent or unknown. The refusal must match exactly: submitter flag, integer exit code, same claim lock, and a positive pid equal to the run's recorded worker pid. Anything else keeps the claim. A `worker_bound` event on the current run, even malformed, remains execution custody and blocks release, as do a live worker, a foreign lock host, or a refusal from an older run.

## How it works

`session_kanban.run_task` calls `asyncio.to_thread(build_kanban_policy, ...)` and drops `connection.resume`; managed execution lifetime stays fenced by the existing submitter watcher. `kanban_owner_recovery.owner_attempt_provably_over` first queries `worker_bound` events joined to the task's current run, then checks worker liveness, then the exact refusal result, before the existing admission-settlement logic. Logs are never used as evidence.

## Code

- `gateway/session_kanban.py`
- `hermes_cli/kanban_owner_recovery.py`

## Tests

**Source regression coverage; not executed in this mirror pass.**

- `tests/gateway/test_kanban_admission_loop_responsiveness.py`: policy build runs off the loop thread and a replay yields one admission even if a viewer resume would fail; a slow policy read does not stall an unrelated loop callback.
- `tests/gateway/test_session_kanban_restart_resume.py`, `test_session_kanban_run_off_loop.py`: expected call sequence is submit only.
- `tests/hermes_cli/test_kanban_unbound_submitter_refusal.py`: release to ready for queued/absent/unknown admission with a live owner; unattributable refusals (wrong pid, lock, flag, exit code, boolean exit) keep the claim; bound, malformed-bound, live, foreign-host and old-run variants keep the claim.

Untested entry paths: a real owner process restart, a real dispatcher submit, and a real crash sweep against a live board.

## Applying

Use the complete segmented replay recipe in `core/README.md` (Applying), not a standalone `git am`. Apply both the gateway and recovery changes together; the recovery rule assumes the refusal event shape already produced by the submitter. Live deployment is unproved.

## Gotchas

Viewers no longer receive a resume event from admission. A refusal is accepted only for the same claim and pid; do not loosen it to match on exit code alone. A malformed bound event deliberately blocks release.

## Why not a plugin

Both changes sit in the gateway owner's admission path and the core crash/recovery sweep, which plugins neither run in nor can reorder.
