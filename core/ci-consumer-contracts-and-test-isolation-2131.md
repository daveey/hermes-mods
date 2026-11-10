# CI consumer contracts and test isolation

Patches **2075, 2087, 2089, 2090, 2091, 2096**.

## Behavior

- 2087 (upstream-sync repair): fork CI contract tests now follow indirection that upstream introduced. A small `just` recipe reader (`tests/ci/just_recipes.py`) resolves workflow `run: just <recipe>` lines to the commands they run, raising on unsupported syntax (parameters, interpolation, shebang, OS variants, imports) so unmodelled recipes fail the invariant instead of passing it. The docker-ci contract executes the hadolint step with a stubbed `docker` and checks that `run` was called, instead of grepping step text. Three gateway tests adapt to upstream: real `ModelSwitchResult`, `_start_gateway_runtime` behind `start_gateway`, a discard-debounce hook on the race-guard fake adapter.
- 2075: a control-diagnostics test waits for the latency record (publication), not just `in_flight == 0`.
- 2089/2090/2091/2096: Kanban DB scratch cleanup, scratch-archive and worktree-teardown fixtures, and the gateway stop-routing fixture inject synthetic empty process inventories so host process discovery (psutil stat on a deleted cwd under the real home) cannot fail or preserve workspaces. Production cleanup and home-I/O protection are unchanged. 2090 only trims lines to keep a fixture file within its size budget.

## How it works

Test-only changes: no production module is altered. `just_recipes.expand`/`recipe_commands` return commands in dependency-first order; `test_just_reader_matches_just_dry_run` compares with real `just --dry-run` when installed, otherwise skips.

## Code

- `tests/ci/just_recipes.py` (new), `tests/ci/test_fast_runner_routing.py`, `tests/ci/test_runtime_install_shape.py`.
- `tests/gateway/test_class_model_switch_persistence.py`, `test_gil_budget.py`, `test_session_race_guard.py`, `test_control_diagnostics.py`.
- `tests/hermes_cli/test_kanban_db.py`, `test_kanban_scratch_gc_archive.py`, `test_kanban_worktree_teardown.py`, `test_gateway_service.py`.

## Tests

**Source regression coverage; not rerun in this documentation pass.** These patches are tests; their own coverage: docker-run contract follows the script (parameterised negative controls), a failing hadolint step fails the contract, reader matches `just --dry-run`, install commands follow recipes, unmodelled recipes fail closed, every runtime install is one shape. The isolation patches were checked in source with unrelated live processes; no separate test asserts the isolation itself. The 2075 wait fix has no separate meta-test.

## Applying

Apply ordinal(s) 2075, 2087, 2089, 2090, 2091, 2096 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. Because 2087 mirrors upstream layout changes (justfile recipes, `scripts/ci/lint_docker.sh`, `_start_gateway_runtime`), apply it only on a snapshot that already contains those upstream shapes; the replay decides. See [git-consumer-fallback-tests.md](git-consumer-fallback-tests.md). No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

- The reader models a small subset of `just`; widening the justfile syntax makes the invariant fail loudly, by design.
- The hadolint contract runs a script with `bash -eo pipefail` and a stub `docker` on PATH.

## Could this be a plugin?

No. These are repository test and CI files, not runtime behaviour.
