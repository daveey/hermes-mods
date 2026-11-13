# Mirror snapshot 3689: inventory and whole-tree proof

## Scope

Append **3653–3689**: 37 non-merge source commits through
`bf08acbc3e1fc396a588e4ffc8ecca841bb0a227`. Published patches 0001–3652, the 46 earlier
bridges, three ancestry-only entries and the verifier remain unchanged. The upstream exclusion
tip and merge-base stay `55b0b2611f683a12450163b9fe423abbd61a9923`. Replay still starts at
`831637fe8d58f050e9aa776b621bb2c7b614dcd1`.

The range `9f1a0b1eb9..bf08acbc3e` contains no merges, so the entries are in plain
`git rev-list --reverse --no-merges` order and need no new bridge
([mirror-replay-3689.json](mirror-replay-3689.json) carries the same 46 hash-pinned bridges).

## Normalization

Exports were written to a staging directory; only exit-0 exports with a single SHA header and a
single `From ` separator were copied. Existing policy substitutions, regex substitutions and the
public no-reply author were applied to every mail. Reviewed mail-only privacy edits (entry 3655,
new test fixture `tests/tools/fixtures/kanban_overlap_same_creator_replay.json`):

- a private network address in two replayed card bodies is replaced by the documentation
  address `192.0.2.17`;
- a cloud account number in one replayed card body is replaced by `123456789012`.

A new fingerprint pins that fixture's source and applied blobs; the applied blob is exactly the
normalized source with those two replacements (checked byte for byte). Entry 3679's later hunk on
the same file applies unchanged.

Earlier fingerprints' reviewed line mappings were checked against the new hunks for the same
paths (no new hunk line needed a mapping). Every re-pinned applied blob equals its new normalized
source plus exactly the previous mapping (checked line by line). Re-pinned:
`hermes_cli/kanban_overlap.py` (entry 3655), `hermes_cli/kanban_placement.py` (entries 3657 and
3688) and `tui_gateway/kanban_remote_notices.py` (entry 3670). One fingerprint added, none dropped
(52 total); the 8 rename rules are unchanged.

The mandatory regex escape repair is carried forward to `repair_hostname_escapes_3689.py` /
`mirror-policy-3689-escape-repair.json` (BEFORE `173f64cda6`, AFTER `54335f367d`, three markers).

## Executed checks

- Full unchanged CLI verifier (Git 2.51): exit 0, including exact inventory and whole-tree
  equivalence (output below).
- Actual README Bash recipe: exit 0, including its full verifier, segmented mail/bridge replay
  (`3184:466`, bridge before 3651, `3650:39`) and the mandatory repaired-tree/marker compilation
  proof (output below).
- Mirror/helper unittest discovery (`python3 -B -m unittest discover -s core -p 'test_*.py'`):
  **187 tests passed**.
- `core/check_coverage.py`: exit 0.
- Private denylist scan of every new or changed file (mails, policies, docs, README):
  zero matches. Feature test suites and live deployment were not run by this pass.

## Full verifier output

```json
{"ancestry_only": 3, "base": "55b0b2611f683a12450163b9fe423abbd61a9923", "bridges": 46, "merge_base": "55b0b2611f683a12450163b9fe423abbd61a9923", "patches": 3689, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "bf08acbc3e1fc396a588e4ffc8ecca841bb0a227"}
```

## Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "54335f367df4b41ea004a945bfe0635fbe8ffdd7", "source_tip": "bf08acbc3e1fc396a588e4ffc8ecca841bb0a227", "whole_tree": "pass"}
```
