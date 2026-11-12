# Root-disk capacity fence for new worker card claims

Patches: **2369**.

## Behavior

When enabled, new card claims are deferred with a typed reason while root disk space is below the configured floor; recovery lifts it automatically and never clears an operator hold. A disabled, unconfigured or missing policy leaves behavior unchanged; an enabled policy with an invalid floor, or an unreadable/negative sample, refuses (fail closed). An installation policy wins over a named profile.

## How it works

`kanban_disk_admission.refusal_reason()` is consulted by `kanban_admission.disk_admission_refused` before the Board lease and again at the final gate after the lease, independently of the updater/operator hold, so it only affects card claims.

## Code

`hermes_cli/kanban_disk_admission.py`, `hermes_cli/kanban_admission.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_disk_admission.py` (refusal before lease and recovery, space falling after lease, operator hold untouched, sample failure, invalid floor, unconfigured host, malformed policy, installation-over-profile).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Config key names are in the source patch and are not restated here. Only new claims are fenced; running work is not interrupted.

## Plugin

Core change; no plugin required.
