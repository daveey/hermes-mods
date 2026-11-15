# Managed-stub relaunch attestation and read-only quiet check

Patches: 3829, 3830.

## Behavior

Recognizes one byte-exact managed-stub relaunch route and feeds its executed-code unknowns into quiet-start refusal. `hermes kanban quiet-hold --check` reports admission gates without taking a lease, spending cooldown or arming an observation episode. Changes to the shared launch template also select the upgrade end-to-end CI lane.

## How it works

`_launch_template.relaunch_prefix` is shared by the producer and attestor. The relaunch detector accepts only isolated `-I` options followed by one `-c` source, reconstructs the embedded script with `ast.literal_eval`, then requires exact prefix/body bytes and the unchanged managed-stub checks. It verifies the bootstrap/import frame chain and outer code object, seeds only the finite prefix stdlib modules with content digests, and recognizes exactly one later exec of the proven embedded stub from the proven prefix frame. Handoff environment presence is reported data, not evidence.

Quiet bootstrap captures the installed ledger; it never installs one late to fabricate provenance. Start/check inventory code unknowns; measured snapshots read only latched unknowns. Missing ledger is pending coverage, unreadable ledger is unknown. The new bookkeeping verb `quiet-observe-check` validates its schema and returns route, registry unknowns, reasons and activity epoch without reserving work. CLI check prints JSON, exits zero only if admitted without reasons, and uses exit 3 for refusal/unavailable/unsupported gateway; lease arguments or observer argv are usage errors. The CI classifier adds `_launch_template.py` to the upgrade route.

## Code

- `gateway/control_dispatch.py`
- `gateway/quiet_control.py`
- `hermes_cli/_launch_template.py`
- `hermes_cli/code_attestation.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_quiet.py`
- `hermes_cli/venv_sync.py`
- `scripts/ci/classify_changes.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/gateway/test_quiet_control.py` (patch 3829).
- `tests/gateway/test_quiet_membership_seeding.py` (patch 3829).
- `tests/hermes_cli/test_code_attestation_relaunch.py` (patch 3829).
- `tests/hermes_cli/test_code_attestation_route.py` (patch 3829).

- **3829:** Real subprocess relaunch cases cover spaced interpreter/current stub, trace-only handoff env and strict refusal of altered flags/argv/root/prefix/body/literals. Launcher-event tests enforce one-shot exact frame/code. Quiet tests pin direct/relaunch agreement, missing ledger pending, cheap latched snapshots, read-only check with unchanged files/epoch, stale-window/refusal parity, unavailable gateway and usage rejection. Existing route/membership fixtures are adapted to provide the captured ledger.
- **3830:** No test file changes in this mail; the only changed assertion surface is CI classifier membership for the shared launch template. Upgrade-lane execution is not established by this documentation pass.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

This supports neither arbitrary relaunch commands nor arbitrary `-c` code. Extra flags, statements, root/argv mismatches, noncanonical literal forms and changed prefix bytes remain refused. A clean check is not a quiet observation proof and does not replace actual lease/observer identity validation at start. CI routing is a classifier change; that mail adds no new test file.

## Plugin?

No. Trust is anchored at bootstrap before ordinary application/plugin execution; a plugin loaded later cannot supply equivalent launch provenance.
