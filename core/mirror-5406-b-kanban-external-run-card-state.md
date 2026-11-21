# Kanban: external-run card state (running / unknown / down) from watch adapters

Mirror entries 5211-5215.

## Behavior

- Cards awaiting kind `ci`/`deploy`/`external` get `external_running`, `external_unknown` or `external_down`, with an `external` object {service, adapter, job, health, reason, url, observed_at, next_due_at} on wait info and `kanban_external*` counts plus `kanban_external_cards` (cap 10) on session rows.
- A pure clock-injected state machine: one failed poll or a 429 is unknown; two consecutive down/unreachable polls, auth_failed, a host silent for more than 2x its interval, or no watch is down; a terminal job wakes and a failed job is not an outage; 429 honors `retry_after` and never counts toward red.
- Adapters: `github_run` and `github_pr_checks` (wrapping the CI progress reader) and `shell` (legacy return codes plus an optional `HERMES_WATCH {json}` line). Watch records are stored as `external_watch` task events; a shared blocker `service-down.<adapter>.<scope>` wakes one probe card and clears on the first ok poll.
- 5212 review fixes: remote shell probes are judged by synced records alone (heartbeat per interval on a synced board); staleness applies before the failing-health return; `auth_failed` asserts one shared blocker while a missing local gh login stays red with an operator warning and wakes nobody; 404/410/422 are this card's ref (card woken, no shared blocker), non-rate-limit 403 is auth, our own quota guard is rate-limited and never red; PR verdict follows required checks.
- 5213: an apply_poll result is bound to the park id and ref it was read for (an old response cannot wake a replacement park); an ok/degraded read clears an open outage before a terminal wake; shell `retry_after` actually delays the next probe (never shortens cadence, ignored unless rate-limited).
- 5214: PR checks need positive success evidence (success, neutral, skipped); stale conclusions count as failed and unknown ones do not wake. 5215: a finished Actions run with no jobs reports its own result; an unfinished one stays queued.

## How it works

- `hermes_cli/kanban_external_state.py`, `kanban_external_watch.py`, `kanban_external_activity.py`, `kanban_external_adapters/{__init__,github,shell}.py`, `kanban_ci_progress.py`, `kanban_wake_check.py`, `kanban_session_activity.py`, `kanban_session_wait_rows.py`.

## Code

- `hermes_cli/kanban_external_state.py`
- `hermes_cli/kanban_external_watch.py`
- `hermes_cli/kanban_external_activity.py`
- `hermes_cli/kanban_external_adapters/github.py`
- `hermes_cli/kanban_external_adapters/shell.py`
- `hermes_cli/kanban_ci_progress.py`
- `hermes_cli/kanban_wake_check.py`

## Tests

Source tests (not executed in this mirror pass): `tests/hermes_cli/test_kanban_external_state.py`, `test_kanban_external_adapters.py`, `test_kanban_external_watch.py`, `test_kanban_external_activity.py`, `test_kanban_external_review.py`, `test_kanban_external_review2.py`, `test_kanban_external_review3.py` (`test_pr_conclusion_controls_real_wake`), `test_kanban_external_review4.py`, `test_kanban_ci_progress_pr.py`. Live GitHub and real remote hosts are faked.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- No per-card last-write-wins table exists in sync, so records are events.
- Hosts that do not advertise heartbeats stay grey, not red.

## Why core instead of a plugin

State derivation reads core parks, wake checks and the Board sync's events; the Desktop display depends on these core fields.
