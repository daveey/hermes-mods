# Mirror snapshot 3423: inventory and whole-tree proof

## Scope

Append **2927–3423**: 497 non-merge source commits through
`4b24631c6ebfb8e42c592fb56abbf707ab5a0d27`. PR #2144 is **3422**.
Published patches 0001–2926, the 44 earlier bridges, three ancestry-only entries and the
verifier remain unchanged. The upstream exclusion tip moves to
`d01ed2bfac3469fa582b9aaeec746f3e123bd78a`, which is also the new merge-base.

The range contains 14 merges. The fork synced upstream `d01ed2bfac` on a side branch whose
integration merge is `91c2b94744`; one new source-derived bridge,
`bridges/upstream-d01ed2bfac-via-91c2b94744.diff` (hash-pinned in
[mirror-replay-3423.json](mirror-replay-3423.json)), precedes entry **3185**, the first
patch written against the synced tree. The other merges (main into the sync branch, and
the two `Merge tested upstream sync … into main` merges) were reproduced exactly by the
series and need no bridge. Commits of the sync side branch are ordered at their first-parent
integration point.

## Normalization

Exports were written to per-entry staging directories; only exit-0 exports with a single SHA
header and a single `From ` separator were copied. Existing policy substitutions, regex
substitutions and the public no-reply author were applied to every mail. Reviewed
mail-only privacy edits:

- 2947: an incident host name in a docstring becomes `example-host` (same wording as the
  existing `gateway/update_checkpoint.py` fingerprint).
- 3157: a private organization marker in a placement regex becomes `example-org`.
- 3365: tailnet addresses in a lifecycle-guard heredoc fixture become documentation addresses
  (`192.0.2.10`–`192.0.2.16`).
- The bridge carries the published wording for the plugin catalog's CGNAT range text.

Earlier fingerprints' reviewed line mappings were carried into the new mails' hunks for the
same paths (context, removed and added lines alike), so every re-pinned applied blob equals
its new normalized source plus exactly the previous mapping (checked line by line), plus the
3157/2947 edits above. Re-pinned: `apps/desktop/electron/remote-down-gate.test.ts`,
`gateway/update_checkpoint.py`, `hermes_cli/kanban_db_workspace.py`,
`hermes_cli/kanban_origin.py`, `hermes_cli/kanban_overlap.py`, `hermes_cli/kanban_placement.py`,
`hermes_cli/kanban_watch.py`, `tests/hermes_cli/test_kanban_placement.py`,
`tests/tui_gateway/test_kanban_remote_notices.py`, `tui_gateway/kanban_remote_notices.py`,
`tui_gateway/kanban_unloaded.py`, `tui_gateway/update_pause.py`. New:
`tests/cron/fixtures/lifecycle_guard_heredoc/core_split_0923.sh` (the 3365 fixture edit).
Dropped (file deleted at the tip): `tests/gateway/test_update_checkpoint_census_agreement.py`.
One new reviewed rename: the placement marker test moved out of
`tests/hermes_cli/test_kanban_placement.py` is published as
`tests/hermes_cli/test_kanban_placement_myapp_marker.py`; its applied blob equals the
normalized source plus the same reviewed mapping as the file it moved from.

The mandatory regex escape repair is carried forward to
`repair_hostname_escapes_3423.py` / `mirror-policy-3423-escape-repair.json`. The snapshot now
carries **three** escaped placement markers (a third marker list was added in this range), so
the overlay replaces three occurrences; its tests cover the 2 and 4 marker-count refusals.

## Executed checks

- Full unchanged CLI verifier (Git 2.51): exit 0, including exact inventory and whole-tree
  equivalence (output below).
- Actual README Bash recipe: exit 0, including its full verifier, segmented mail/bridge replay
  (`2255:929`, bridge before 3185, `3184:239`) and the mandatory repaired-tree/marker compilation
  proof (output below).
- Mirror/helper unittest discovery (`python3 -B -m unittest discover -s core -p 'test_*.py'`):
  **115 tests passed**.
- Private denylist scan of every new or changed file (mails, bridge, policies, docs, README):
  zero matches. Feature test suites and live deployment were not run by this pass.

## Full verifier output

```json
{"ancestry_only": 3, "base": "d01ed2bfac3469fa582b9aaeec746f3e123bd78a", "bridges": 45, "merge_base": "d01ed2bfac3469fa582b9aaeec746f3e123bd78a", "patches": 3423, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "4b24631c6ebfb8e42c592fb56abbf707ab5a0d27"}
```

## Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "929408adbcffa91113d9f61750933e857eff7981", "source_tip": "4b24631c6ebfb8e42c592fb56abbf707ab5a0d27", "whole_tree": "pass"}
```
