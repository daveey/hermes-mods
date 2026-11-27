# Deterministic fixture sampling and composed transport CI

Patch coverage: 6240, 6350, 6352, 6360, 6361, 6362, 6363, 6364.

## Behavior

Test fixtures synchronize with the actual child-output and clock/sample they consume rather than racing a second observation. Catalog clone fixtures exclude transient object-store material and PASS assertions distinguish real gate results from unrelated output.

A separate composed origin-transport workflow runs the entire transport test directory with an explicitly pinned courier client; empty or skipped runs fail rather than silently passing.

## How it works

Node dependency tests drain child output before snapshotting the result. Restart-cooldown tests isolate their clock from schedulers, disk-space tests compare the same statvfs sample, and CI intake documentation records routing rather than proving a new runtime behavior.

The composed workflow is restricted to trusted main/manual/scheduled events and an environment-scoped read credential; ordinary pull-request CI stays secret-free. Checkout credentials are not persisted. The lane uses the heavier runner and the managed test interpreter directly so the required transport environment survives. A weekly check detects client changes beyond the configured pin; manual dispatch can select a candidate ref. The JUnit checker refuses skips and no tests.

## Code

Affected implementation and integration paths in the source series:

- `.github/workflows/origin-transport.yml`
- `scripts/ci/check_origin_transport_junit.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests-js/node-deps.test.mjs`
- `tests/ci/test_plugin_catalog_ci_confinement.py`
- `tests/hermes_cli/test_spawn_gateway_restart_cooldown.py`
- `tests/hermes_cli/test_kanban_scratch_archive_retention.py`
- `tests/ci/test_origin_transport_ci.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

Workflow definition is not evidence that credentials are provisioned or the composed job ran. The separate secret-bearing lane is not presented as a universal PR required gate. Internal intake narratives and external repository coordinates are intentionally not reproduced in this public feature description.

## Why core, not a plugin?

These are repository CI contracts and test-runner fixtures, not runtime plugin behavior.
