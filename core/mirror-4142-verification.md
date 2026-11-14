# Mirror snapshot 4142: inventory and whole-tree proof

## Scope

Append **4060–4142**: 83 non-merge source commits through
`133dd0ff8e225eb161d6934e19d45bc22e4e5f78`. Published patches 0001–4059,
47 published bridges, three ancestry-only entries and the verifier remain unchanged.

The range contains one upstream sync: upstream `ae1ff71c39d8527227400c8e71acf9c57a8a59c6`
entered a fork side branch through merges `e4450f549b` (upstream `491f984583`) and
`1ec97dfdb9`, and that side branch reached main through integration merge `a3fe7bf2c7`.
The upstream exclusion tip and merge-base move to `ae1ff71c39d8527227400c8e71acf9c57a8a59c6`;
replay still starts at `831637fe8d58f050e9aa776b621bb2c7b614dcd1`.

Ordering: the 54 mainline entries 4060–4113, then the five side-branch entries 4114–4118
at the integration merge, then the 24 post-merge entries 4119–4142.

## Bridges

Two new hash-pinned bridges, recorded in `mirror-replay-4142.json` (49 bridges total):

- `bridges/upstream-491f984583-agents-md-via-e4450f549b.diff` before 4116 (`source_merge`
  `e4450f549b`): `gateway/AGENTS.md` context as of that side-branch merge, which 4116 then trims.
- `bridges/upstream-ae1ff71c39-via-a3fe7bf2c7.diff` before 4118 (`source_merge` `1ec97dfdb9`):
  the 302 paths upstream changed between `b51a30c461` and `ae1ff71c39`, plus the side branch's
  fork-side merge resolution in `hermes_cli/kanban_admission.py` and `hermes_cli/kanban_gateway_drain.py`.
  Path sets were compared against the upstream delta and the mainline entries before pinning.

Both bridges were scanned with the private denylist (zero matches). gitleaks flagged three
lines of the `ae1ff71c39` bridge, all in upstream test fixtures (a Discord adapter test and the
stream-consumer secret holdback test's synthetic tokens); they get line-scoped `.gitleaksignore`
entries for that bridge file only.

## Normalization

All 83 exports completed in a task-local staging directory. Each keeps its source SHA header,
full diff and one mail separator. Existing literal and regex substitutions and public no-reply
author normalization were applied. Historical published bytes were not rewritten.

One historical fingerprint was re-pinned: `tui_gateway/kanban_remote_notices.py`
(`accf5134ec16ce18a7dea1f34d54c94d2daea300` / `2f7cb9199d7819a28a5eaa9d73c91c7a800ca9c5`).
The new applied blob is exactly the new normalized source with the old pair's single reviewed line
mapping (`prove_repin`: EXACT). No fingerprint was added or removed.

The mandatory additive regex repair is bound to snapshot 4142 by
`repair_hostname_escapes_4142.py` and `mirror-policy-4142-escape-repair.json`. The
`hermes_cli/kanban_placement.py` preimage `5965a73dce84565aacea66d2091d05baeead4833` and
postimage `8f561a6010a50916acb0fc96c6016084a5ecd988` are unchanged from snapshot 4059.

## Executed checks

- Mirror/helper unittest discovery (`core/test_*.py`, Git 2.51): **219 tests passed**.
- Final candidate private denylist scan: **110 files, zero matches**.
- New snapshot unchanged full verifier: **exit 0**, proving the exact 4142-entry inventory and full normalized source tree.
- Fingerprint re-pin proof for `tui_gateway/kanban_remote_notices.py`: exact.
- `core/check_coverage.py`, whitespace checks on docs/JSON/Python and the unmodified pre-commit privacy/coverage hook: **exit 0**.
- Actual README Bash recipe (Git 2.51): **exit 0**, including its own full verifier, all segmented mail/bridge application (final slices `3718:397`, `4115:2`, `4117:25`), the mandatory additive repair and the repaired whole-tree/marker compilation checks.

## Full verifier output

```json
{"ancestry_only": 3, "base": "ae1ff71c39d8527227400c8e71acf9c57a8a59c6", "bridges": 49, "merge_base": "ae1ff71c39d8527227400c8e71acf9c57a8a59c6", "patches": 4142, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "133dd0ff8e225eb161d6934e19d45bc22e4e5f78"}
```

## Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "8f561a6010a50916acb0fc96c6016084a5ecd988", "source_tip": "133dd0ff8e225eb161d6934e19d45bc22e4e5f78", "whole_tree": "pass"}
```

Feature test suites and live display/deployment were not run by this pass. Feature
documents describe the staged diffs, not fresh execution results.
