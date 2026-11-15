# Mirror snapshot 4059: inventory and whole-tree proof

## Scope

Append **3728–4059**: 332 non-merge source commits through
`3c0d856312756637a976519ed8315cffa4356c33`. Published patches 0001–3727,
47 bridges, three ancestry-only entries and the verifier remain unchanged.
No merge commits occur between snapshots 3727 and 4059. The upstream exclusion tip
and merge-base remain `b51a30c461ee663313ce89d01947bb10b55a619c`; replay still starts
at `831637fe8d58f050e9aa776b621bb2c7b614dcd1`.

## Normalization

All 332 exports completed successfully in a staging directory. Each retains its source
SHA header, full diff and one mail separator. Existing literal and regex substitutions
and public no-reply author normalization were applied. Historical published bytes
were not rewritten. No unpublished hunk line needed an additional historical mapping.

Seven historical fingerprints were re-pinned: `hermes_cli/kanban_capability_registry.py`,
`hermes_cli/kanban_overlap.py`, `hermes_cli/kanban_placement.py`,
`tests/cron/test_lifecycle_guard_session_aliases.py`,
`tests/tools/fixtures/kanban_overlap_same_creator_replay.json`,
`tui_gateway/kanban_remote_notices.py` and `tui_gateway/kanban_unloaded.py`.
Each predicted applied blob is exactly its new normalized source with the old
source/applied pair's reviewed line mappings. No fingerprint was added or removed.
The complete series replay must independently verify those predicted blobs.

The mandatory additive regex repair is bound to snapshot 4059 by
`repair_hostname_escapes_4059.py` and `mirror-policy-4059-escape-repair.json`.
Its exact preimage is `5965a73dce84565aacea66d2091d05baeead4833`, and its postimage is
`8f561a6010a50916acb0fc96c6016084a5ecd988`; only three escaped word-boundary markers
change. The real repaired bytes pass the AST marker compile guard.

## Executed checks

- Published snapshot 3727 full unchanged verifier: exit 0 before any new mails were copied.
- Mirror/helper unittest discovery: **211 tests passed**.
- Draft mail private denylist scan: **332 files, zero matches**.
- New snapshot unchanged full verifier: **exit 0**, proving the exact 4059-entry inventory and full normalized source tree.
- Final candidate private denylist scan: **482 files, zero matches**.
- `core/check_coverage.py`, docs/JSON/Python whitespace checks and the unmodified pre-commit privacy/coverage hook: **exit 0**.
- Actual README Bash recipe: **exit 0**, including its own full verifier, all segmented mail/bridge application, final slice `3718:341`, mandatory additive repair and repaired whole-tree/marker compilation checks.
- Independent review verified every new mail's complete normalized source parity, all seven fingerprint re-pins and all local documentation links; no blocking defects were found.

## Full verifier output

```json
{"ancestry_only": 3, "base": "b51a30c461ee663313ce89d01947bb10b55a619c", "bridges": 47, "merge_base": "b51a30c461ee663313ce89d01947bb10b55a619c", "patches": 4059, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "3c0d856312756637a976519ed8315cffa4356c33"}
```

## Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "8f561a6010a50916acb0fc96c6016084a5ecd988", "source_tip": "3c0d856312756637a976519ed8315cffa4356c33", "whole_tree": "pass"}
```

Feature test suites and live display/deployment were not run by this pass. Feature
reports marked as source evidence are not substituted for independently executed checks.
