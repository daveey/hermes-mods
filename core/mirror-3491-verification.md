# Mirror snapshot 3491: inventory and whole-tree proof

## Scope

Append **3474–3491**: 18 non-merge source commits through
`a0307c048cc5ca6a6dfbd36dc0d12064883b2930`.
Published patches 0001–3473, the 45 bridges, three ancestry-only entries and the verifier remain
unchanged. The upstream exclusion tip stays `d01ed2bfac3469fa582b9aaeec746f3e123bd78a`, which is
still the merge-base. The range contains no merges, so no bridge is added; the replay plan only
moves its `source_tip`.

## Normalization

Exports were written to a staging directory; only exit-0 exports with a single SHA header and a
single `From ` separator were copied. Existing policy substitutions, regex substitutions and the
public no-reply author were applied to every mail. No new mail-only privacy edit was needed.

No new patch touches a fingerprinted path, so the carried line mappings changed no mail line and
every fingerprint keeps its 3473 pin; no fingerprint is added, re-pinned or dropped, and renames
are unchanged. The policy differs from `mirror-policy-3473.json` only in `source_tip`.

The mandatory regex escape repair is carried forward to `repair_hostname_escapes_3491.py` /
`mirror-policy-3491-escape-repair.json` with the same BEFORE (`2579ffc51b`) and AFTER
(`b8c1c7f48b`) placement blobs; the snapshot still carries three escaped placement markers.

## Executed checks

- Full unchanged CLI verifier (Git 2.51) with the frozen policy and replay plan: exit 0, including
  exact inventory and whole-tree equivalence (output below).
- Actual README Bash recipe: exit 0, including its full verifier, segmented mail/bridge replay
  (`2255:929`, bridge before 3185, `3184:307`) and the mandatory repaired-tree/marker compilation
  proof (output below).
- Mirror/helper unittest discovery (`python3 -B -m unittest discover -s core -p 'test_*.py'`):
  **131 tests passed**.
- `core/check_coverage.py`: exit 0.
- Private denylist scan of every new or changed file (mails, policies, docs, README): zero
  matches. Feature test suites and live deployment were not run by this pass.

## Full verifier output

```json
{"ancestry_only": 3, "base": "d01ed2bfac3469fa582b9aaeec746f3e123bd78a", "bridges": 45, "merge_base": "d01ed2bfac3469fa582b9aaeec746f3e123bd78a", "patches": 3491, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "a0307c048cc5ca6a6dfbd36dc0d12064883b2930"}
```

## Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "b8c1c7f48bcb92f7996e8c8f5762aa081c6adbaf", "source_tip": "a0307c048cc5ca6a6dfbd36dc0d12064883b2930", "whole_tree": "pass"}
```
