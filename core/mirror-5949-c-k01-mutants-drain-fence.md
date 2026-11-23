# Drain-fenced offline mutation helper

Patch: **5948**. Routes every subprocess in the Desktop K01 mutation helper through the worker drain fence.

## Behavior

The offline helper `apps/desktop/scripts/k01-mutants.py` no longer spawns children directly. Each child (the git file listing and every vitest run) is started under a drain lease held through the child's exit, even if the parent dies. During draining or sealed phases a new spawn is refused before the child exists; a lease already held stays counted. Timeout or interruption kills and reaps the direct child and releases the lease; a spawn failure also releases it. Without an activated drain the child just runs.

Child environments are built from the scrubbed local subprocess environment plus explicit overrides, so ambient secrets are not inherited. The mutation inventory and verdict logic are unchanged; the script body moved into `main()` and the root is added to `sys.path` for direct execution.

## How it works

`run_command(command, timeout, env, check, **kwargs)` wraps `worker_drain_fence.popen_worker(subprocess.Popen, ..., drain_purpose='desktop.k01-mutants')`, uses `communicate(timeout)`, builds a `CompletedProcess`, and honors `check`. The spawn inventory `hermes_cli/worker_drain_spawn_inventory.json` records the script as `fenced` (0 direct, 1 routed) and reorders two entries alphabetically.

## Code

- `apps/desktop/scripts/k01-mutants.py`
- `hermes_cli/worker_drain_spawn_inventory.json`

## Tests

**Source regression coverage; not executed in this mirror pass.**

- `tests/hermes_cli/test_k01_mutants_drain.py`: refusal before spawn in draining/sealed phases; real child inherits and holds the lease fd until exit and a second spawn is refused mid-drain; timeout reaps and releases; non-zero exit capture and `check`; spawn failure releases; unactivated child still runs with the supplied env.

These tests use fixture identity and POSIX-only behavior. No native K01 probe and no mutation campaign (vitest over the Desktop tree) is run, so the helper's end-to-end mutation results are untested here.

## Applying

Use the complete segmented replay recipe in `core/README.md` (Applying), not a standalone `git am`. Requires the drain fence module and scrubbed-environment builder already in core. Live deployment is unproved.

## Gotchas

Descendants that keep the inherited lease descriptor stay visible to the drain after the direct child is reaped. Output capture now goes through `communicate`, so large outputs buffer in memory when piped. The helper is an offline development tool, not a runtime feature.

## Why not a plugin

It is a core repository script governed by the core drain-fence inventory; a plugin cannot change that audit or the fenced spawn route.
