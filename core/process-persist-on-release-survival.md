# persist_on_release jobs survive gateway restarts and run-scope stops

Patches: **1904** (`441a5464ba`, `fix(process): persist_on_release jobs survive gateway restarts and Kanban run-scope stops [daveey]`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- A gateway restart sweeps background jobs with lifecycle source `gateway_restart`, which spares `persist_on_release` jobs (the next gateway re-adopts them); a real stop still kills persisted jobs it spawned but never one it only adopted. A `persist_on_release` job spawned inside a Kanban run scope gets its own transient scope in `hermes-kanban.slice` with the run scope's memory cap, so a run-scope OOM stop no longer takes it.

## How it works

See Code; `format_uptime_short` moved to `tools/process_registry_uptime.py` for the line cap.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1904: Hub 2026-11-03: paid GPU-rental watchdogs started by Kanban workers with persist_on_release died with gateway restarts and worker OOMs, leaving rentals billing unsupervised.

## Code

`gateway/run_shutdown.py`, `tools/process_registry.py`, `tools/process_registry_persist_scope.py`, `tools/process_registry_uptime.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/gateway/test_persist_job_survives_gateway_restart.py`: `test_worker_started_persisted_job_survives_gateway_shutdown_sweep`, `test_restart_spares_gateway_own_persisted_job_but_kills_volatile`, `test_final_stop_still_kills_gateway_own_persisted_job`, `test_explicit_kill_still_reaches_adopted_persisted_job`
- `tests/gateway/test_shutdown_kill_restart_source.py`: `test_post_interrupt_sweep_source_follows_restart`, `test_off_loop_sweep_forwards_restarting`, `test_restart_source_is_a_lifecycle_sweep`
- `tests/tools/test_persist_job_own_scope.py`: `test_run_scope_cgroup_parses_only_kanban_run_scopes`, `test_persisted_job_in_kanban_run_scope_gets_own_scope`, `test_volatile_job_stays_in_run_scope`, `test_persisted_job_outside_kanban_run_scope_is_unchanged`, `test_real_persisted_job_lands_outside_run_scope`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
