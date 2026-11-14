# Foreign process-management regression contracts

Mirror ordinals: 3760, 3762.

## Behavior

- Test-only additions pin which persisted jobs a different process may poll or kill, and how completion receipts settle.
- A recorded kill marker is visible to poll/log/wait/kill before the supervisor writes its final receipt; a later real exit receipt supersedes that provisional marker.
- The persisted recorder must preserve a failing shell command's exit status.

## How it works

No production implementation is changed by these patches. Disposable live-process fixtures exercise the foreign registry fallback against writer/process birth identity, prefix resolution and sandbox/container exclusions. The adoption matrix constructs a gone PID with a log and kill marker but no exit receipt, queries each foreign operation, then writes the supervisor's authoritative receipt.

## Code

- `(test-only follow-up; production implementation is unchanged by these ordinals)`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/tools/test_process_foreign_gates.py` — A real persisted command exits 3; a foreign live writer/job is served, while own-writer, dead-writer orphan, ambiguous/short prefix, foreign namespace and other-sandbox cases are not found. Wait returns an arriving exit-9 receipt promptly or waits the settle grace before unknown status; kill really signals a fixture job.
- `tests/tools/test_process_restart_adoption_matrix.py` — All four foreign operations return the recorded killed metadata before settlement, with kill reporting already_exited; an eventual exit-7 receipt wins and drops provisional termination_source.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

The fallback is not orphan adoption. A missing receipt cannot be reported as success. These tests use real disposable processes plus controlled persisted records, not production jobs.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.
