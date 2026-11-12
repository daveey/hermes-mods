# Stopped build-owner fixture blocks without a fork

Patches: **2868**.

## Behavior

Test-only: a stopped-owner build-lock regression waits in a shell builtin read instead of forking sleep repeatedly, so group SIGSTOP cannot strand the fixture shell in an uninterruptible vfork wait.

## How it works

**2868:** pass a piped stdin through the background runner and relay; the pnpm owner shim waits on `read`, and the test releases it by writing a line. Product lock-watcher behavior and timeouts are unchanged.

## Code

Test-only changes; no production implementation is modified.

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`tests-js/node-deps.test.mjs` retains the stopped/killed/identity-changed owner cases and the assertion that an invalidated owner cannot later write its build output.

- `tests-js/node-deps.test.mjs`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

A shell in vfork wait may look like D rather than T although its job has stopped. The stdin barrier removes that fixture race; do not weaken the watcher’s kill assertion.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.
