# Content-keyed reuse of the macOS state lane

Mirror ordinals: 3734.

## Behavior

- A same-repository pull request may reuse its own earlier successful macOS state-lane run when the changed routing surface has identical content.
- Unrelated workflow/action edits no longer force that lane; its workflow/caller, relevant state files and general CI configuration still route it.
- Empty or capped comparisons, releases, forks and manual dispatch do not receive unsafe reuse.

## How it works

`classify_changes.hermes_state_files` centralizes the routing surface. `macos_state_reuse.py` hashes HEAD tree entries, including modes/blob IDs and an explicit absent entry for deletions, for changed routing files plus the lane workflow. `ci.yaml` computes a key only for a selected same-repository non-release PR, does a lookup-only cache restore, and gates the lane off only on a true hit. The required-check job records/saves a key only after the lane actually ran successfully. Lookup/key failures retain execution rather than manufacturing a passing receipt.

## Code

- `.github/workflows/ci.yaml`
- `scripts/ci/classify_changes.py`
- `scripts/ci/macos_state_reuse.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/ci/test_classify_changes.py` — Workflow/action edits are contrasted with the state workflow and CI caller so unrelated changes do not force the state lane.
- `tests/ci/test_macos_state_reuse.py` — Temporary Git trees prove unrelated edits keep keys while surface/blob/mode/deletion changes alter them; bounded-diff eligibility, CLI output/error behavior, PR/fork/release gating, cache wiring, success-only recording, reused required-check behavior and absence of local actions in the lane are asserted.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

The key intentionally does not hash every unchanged state file: unrelated mainline surface changes remain covered by their own PRs and scheduled runs. Cache access is an optimization, never the only way to satisfy the lane.

## Plugin?

No. This is repository CI routing and cache receipt wiring, not an agent or Desktop extension.
