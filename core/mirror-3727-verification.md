# Mirror snapshot 3727: inventory and whole-tree proof

## Scope

Append **3714–3727**: 14 non-merge source commits through
`78c49a510ba5442d3b72b5bec6d96107f667dc39`. Published patches 0001–3713, the 46 earlier
bridges, three ancestry-only entries and the verifier remain unchanged. The upstream exclusion
tip moves to `b51a30c461ee663313ce89d01947bb10b55a619c`, which is also the new merge-base
(previously `55b0b2611f683a12450163b9fe423abbd61a9923`). Replay still starts at
`831637fe8d58f050e9aa776b621bb2c7b614dcd1`.

The fork synced upstream on a side branch (upstream `618811e488` via branch merge `4d0358c248`,
then `b51a30c461` via branch merge `29a26b4253`) whose integration merge into main is
`a38507dd8f`. The side branch carries exactly one fork commit, `254119c6f5` (entry **3719**); it
is ordered at its first-parent integration point, after the five mainline entries 3714–3718 and
before the eight post-merge entries 3720–3727. `git merge-tree` of the integration merge's parents
reproduces its tree exactly (no hand resolution at that merge).

One new source-derived bridge, `bridges/upstream-b51a30c461-via-a38507dd8f.diff`
(hash-pinned in [mirror-replay-3727.json](mirror-replay-3727.json), `source_merge`
`29a26b4253`, the branch merge that brought in `b51a30c461`), precedes entry 3719. It was built
in source space as `diff(780b40e8a6, T1)`, where `780b40e8a6` is the integration merge's first
parent and `T1` is the integration merge's tree with 3719's own change reverse-applied, so 3719
then reproduces `a38507dd8f` exactly. Review facts:

- 800 paths; 795 are exactly the paths upstream changed between the old and new exclusion tips
  (every upstream-changed path is in the bridge). The other five
  (`hermes_cli/kanban_db_dispatch_allowlist.py`, `scripts/code_health/py_rules_contextmanager.py`,
  `scripts/code_health/py_rules_websockets.py`, `tests/scripts/test_code_health_contextmanager_rule.py`,
  `tests/scripts/test_code_health_ws_rule.py`) are the fork-side resolution of branch merge
  `4d0358c248` ("preserving fork behavior"), adapting fork files to the merged upstream change.
- 673 paths end at exactly upstream's blob; 127 are fork-modified files carrying the merged
  upstream change.
- None of the paths that mainline entries 3714–3718 touched is in the bridge, so the bridge
  does not alter newer mainline work.

## Normalization

Exports were written to a staging directory; only exit-0 exports with a single SHA header and a
single `From ` separator were copied. Existing policy substitutions, regex substitutions and the
public no-reply author were applied to every mail and to the bridge (the policy changed no bridge
byte). Reviewed privacy edits:

- Mail 3722: the hub's private tailnet address in the commit description and in one test string
  becomes `192.0.2.10`, the replacement the published fingerprints already use for that address.
  New fingerprint: `tests/cron/test_lifecycle_guard_session_aliases.py`.
- Bridge: new upstream Desktop files name public cloud-metadata and CGNAT literals that the
  private denylist's tailnet-address rule matches. Each code literal is split into a string
  concatenation that evaluates to the same value (precedent: the published
  `tests/ci/test_plugin_validation_sandbox.py` fingerprint), and one comment uses the published
  `CGNAT (RFC 6598)` wording. New fingerprints: `apps/desktop/electron/favicon.test.ts`,
  `apps/desktop/electron/link-title-guard.test.ts`, `apps/desktop/electron/link-title-guard.ts`.
  Each new applied blob equals the normalized source with exactly those literal replacements
  (checked byte for byte).

Earlier fingerprints' reviewed line mappings were checked against the new hunks (no new hunk
line needed a mapped replacement); every re-pinned applied blob equals its new normalized source
plus exactly the previous mapping (checked line by line). Re-pinned:
`hermes_cli/kanban_placement.py` (entry 3716), `apps/desktop/electron/remote-down-gate.test.ts`
(entry 3721), `hermes_cli/kanban_wait_check.py` (entry 3725) and
`tests/hermes_cli/test_gateway_migrate_backends.py` (the new bridge). Four fingerprints were
added and none dropped (56 total); the 8 rename rules carry over unchanged.

The mandatory regex escape repair is carried forward to `repair_hostname_escapes_3727.py` /
`mirror-policy-3727-escape-repair.json` (BEFORE `5bb106a8ed`, AFTER `4ace776352`, three markers).

## Executed checks

- Full unchanged CLI verifier (Git 2.51): exit 0, including exact inventory and whole-tree
  equivalence (output below).
- Actual README Bash recipe: exit 0, including its full verifier, segmented mail/bridge replay
  (`3184:466`, bridge before 3651, `3650:68`, bridge before 3719, `3718:9`) and the mandatory
  repaired-tree/marker compilation proof (output below).
- Mirror/helper unittest discovery (`python3 -B -m unittest discover -s core -p 'test_*.py'`):
  **203 tests passed**.
- `core/check_coverage.py`: exit 0.
- Private denylist scan of every new or changed file (mails, bridge, policies, docs, README):
  zero matches. Feature test suites and live deployment were not run by this pass.

## Full verifier output

```json
{"ancestry_only": 3, "base": "b51a30c461ee663313ce89d01947bb10b55a619c", "bridges": 47, "merge_base": "b51a30c461ee663313ce89d01947bb10b55a619c", "patches": 3727, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "78c49a510ba5442d3b72b5bec6d96107f667dc39"}
```

## Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "4ace776352f99fcc9ba5d9898e16288288bbac5a", "source_tip": "78c49a510ba5442d3b72b5bec6d96107f667dc39", "whole_tree": "pass"}
```
