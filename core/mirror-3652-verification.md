# Mirror snapshot 3652: inventory and whole-tree proof

## Scope

Append **3623–3652**: 30 non-merge source commits through
`9f1a0b1eb92c02a4cafa5663a9853c38689a3c70`. Published patches 0001–3622, the 45 earlier
bridges, three ancestry-only entries and the verifier remain unchanged. The upstream exclusion
tip moves to `55b0b2611f683a12450163b9fe423abbd61a9923`, which is also the new merge-base
(previously `d01ed2bfac3469fa582b9aaeec746f3e123bd78a`). Replay still starts at
`831637fe8d58f050e9aa776b621bb2c7b614dcd1`.

The range contains 53 merges, almost all upstream pull-request merges inside the synced
upstream history (excluded by the new base). The fork synced upstream on a side branch
(upstream `81162128b7`, then `55b0b2611f`, then main merged into the branch three times) whose
integration merge into main is `db5a056366`. The side branch carries exactly one fork commit,
`ed89c2fd9b` (entry **3651**); it is ordered at its first-parent integration point, after the
29 mainline entries 3623–3650 and before the post-merge entry 3652.

One new source-derived bridge, `bridges/upstream-55b0b2611f-via-db5a056366.diff`
(hash-pinned in [mirror-replay-3652.json](mirror-replay-3652.json), `source_merge`
`b4c4e307a9`, the branch merge that brought in `55b0b2611f`), precedes entry 3651. It was built
in source space as `diff(559d6ee3a0, T1)`, where `559d6ee3a0` is the integration merge's first
parent and `T1` is the integration merge's tree with 3651's own change reverse-applied, so 3651
then reproduces `db5a056366` exactly. Review facts:

- 950 paths; 947 are exactly the paths upstream changed between the old and new exclusion tips.
  The other three (`apps/desktop/src/store/session-lineage.ts`, `gateway/run_adapter_status.py`,
  `gateway/run_turn_session_info.py`) are the hand resolution of merge `278caede6c` carrying
  upstream changes into fork-split files; no later branch merge touches them.
- 808 paths end at exactly upstream's blob; 142 are fork-modified files carrying the merged
  upstream change.
- None of the paths that mainline entries 3623–3650 touched is in the bridge, so the bridge
  does not alter newer mainline work.

## Normalization

Exports were written to a staging directory; only exit-0 exports with a single SHA header and a
single `From ` separator were copied. Existing policy substitutions, regex substitutions and the
public no-reply author were applied to every mail and to the bridge (the bridge needed no
substitution). Reviewed bridge-only privacy edit:

- An upstream contributor-mapping file whose name is an email address matching the private
  denylist is published as `contributors/emails/upstream-contributor@example.com` (content
  unchanged). A new rename rule pins identical source and applied blobs, the same precedent as
  the existing `contributors/emails/*` rules (8 rename rules now).

Earlier fingerprints' reviewed line mappings were carried into the new hunks for the same paths;
every re-pinned applied blob equals its new normalized source plus exactly the previous mapping
(checked line by line). Re-pinned: `hermes_cli/kanban_placement.py` (entry 3625),
`hermes_cli/kanban_watch.py` and `tests/tools/test_kanban_watch_park.py` (entry 3641), and
`hermes_cli/update_cmd_posix_pause.py` (the new bridge). No fingerprint was added or dropped
(51 total).

The mandatory regex escape repair is carried forward to `repair_hostname_escapes_3652.py` /
`mirror-policy-3652-escape-repair.json` (BEFORE `2dfd4a9e7b`, AFTER `76d0895836`, three markers).

## Executed checks

- Full unchanged CLI verifier (Git 2.51): exit 0, including exact inventory and whole-tree
  equivalence (output below).
- Actual README Bash recipe: exit 0, including its full verifier, segmented mail/bridge replay
  (`3184:466`, bridge before 3651, `3650:2`) and the mandatory repaired-tree/marker compilation
  proof (output below).
- Mirror/helper unittest discovery (`python3 -B -m unittest discover -s core -p 'test_*.py'`):
  **179 tests passed**.
- Private denylist scan of every new or changed file (mails, bridge, policies, docs, README):
  zero matches. Feature test suites and live deployment were not run by this pass.

## Full verifier output

```json
{"ancestry_only": 3, "base": "55b0b2611f683a12450163b9fe423abbd61a9923", "bridges": 46, "merge_base": "55b0b2611f683a12450163b9fe423abbd61a9923", "patches": 3652, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "9f1a0b1eb92c02a4cafa5663a9853c38689a3c70"}
```

## Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "76d089583607fd19302285028581c3820379eccc", "source_tip": "9f1a0b1eb92c02a4cafa5663a9853c38689a3c70", "whole_tree": "pass"}
```
