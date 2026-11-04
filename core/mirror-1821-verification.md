# Mirror 1821 verification

Frozen source: `2050733bc6571383fe954bea422f8da0f8cdd67d` (daveey/hermes PR #744 landing commit).
Upstream exclusion: `f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab` (unchanged from the 1702 pass).
This pass appends **119** previously unpublished non-merge commits as **1703–1821**, in
`git rev-list --reverse --no-merges` order, numbered from the last published ordinal + 1.
The published 0001–1702 patch files and their ordinals are unchanged. Source commits landing
after this frozen tip (including the fork's later upstream merge `27576ed88c6`) belong to the
next snapshot.

## Why this tip

No upstream merge reached the fork between the 1702 tip (`871e0a9e216`) and `2050733bc65`:
`git merge-base upstream/main <tip>` is `e8639fd4312` for both, and no merge in
`871e0a9e216..2050733bc65` has a parent on upstream. The existing seven-bridge replay plan
therefore applies unchanged; only its `source_tip` moves. The fork's next upstream merge
(`27576ed88c6`, upstream `fd267bb324`) comes after this tip and needs its own bridge.

## Executed acceptance

The unchanged verifier CLI exited **0** on the complete inventory and normalized full-tree comparison
(source repo: a `git clone --bare --shared` of the hub's fork checkout):

```sh
python3 -B core/verify_mirror.py --source-repo ../src.git \
  --source-tip 2050733bc6571383fe954bea422f8da0f8cdd67d \
  --base f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab \
  --patch-dir core/patches --policy core/mirror-policy-1821.json \
  --replay-plan core/mirror-replay-1821.json
```

```json
{"ancestry_only": 3, "base": "f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab", "bridges": 7, "merge_base": "e8639fd4312e5102d6f10c76b56406cb01c19092", "patches": 1821, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "2050733bc6571383fe954bea422f8da0f8cdd67d"}
```

The README **Applying** recipe (last slice now `"${patches[@]:1656:165}"`) was also run as written
in a throwaway worktree at `831637fe8d5`: every `git am`/`git apply` step succeeded (exit 0), and the
resulting commit's tree passed `verify_mirror.compare` against the frozen tip under `mirror-policy-1821.json`.

## Privacy

Each new patch was normalized with the standard substitutions and `From:` rewrite, then scanned
with the private denylist (`grep -cEi`, every file `:0`). One fixture hit remained:
`tests/tools/kanban_overlap_failure_cases.json` (patch 1800) quotes an ssh command with the hub's
tailnet address. The mail patch replaces `ubuntu@<hub address>` with `ubuntu@192.0.2.10`
(documentation range, as earlier patches do). Each new patch has exactly one `From <sha>` separator.

## Reviewed fingerprints

New: `tests/tools/kanban_overlap_failure_cases.json` = source blob `1bc58a9c34f8`, applied blob
`3949eeaeb2a4`. The applied blob is exactly the source blob with that one address replaced
(computed with `sed` + `git hash-object`, then matched by the replay).

Re-pins: seven files that already had reviewed fingerprints changed in this batch
(`hermes_cli/kanban_overlap.py`, `hermes_cli/kanban_placement.py`,
`tests/agent/test_model_routing_row_route_desync.py`, `tests/ci/test_plugin_validation_sandbox.py`,
`tests/hermes_cli/test_kanban_placement.py`, `tui_gateway/kanban_remote_notices.py`,
`tui_gateway/update_pause.py`). For each, the added/removed lines of the old pair's diff and of the
new pair's diff were compared and are byte-identical (same reviewed normalization, shifted
context only). No global substitution or rename rule was added.

## Tests run

On the source tip tree (`scripts/run_tests.sh`, scratch `HERMES_HOME`): 6 files, **401 passed, 0
failed**: `tests/tui_gateway/test_actionable_block_notices.py`,
`tests/tui_gateway/test_kanban_failure_notice_text.py`,
`tests/tools/test_kanban_create_overlap_singleton.py`,
`tests/tools/test_kanban_create_overlap_failure.py`, `tests/hermes_cli/test_kanban_placement.py`,
`tests/hermes_cli/test_kanban_deploy_window.py`. Other files named in the feature docs were not
rerun by this pass. (Running the same files together under `pytest -n 4` showed three
`test_non_deploy_waits_ignore_a_healthy_window[kind1-*]` failures that pass in the file's own
process; the canonical runner isolates each file.)

## Feature docs

35 new feature docs plus this report, indexed in README § Mirror follow-ups 1703–1821. Each doc
lists its patches, behavior, code, the tests landed with the source commits, and patches that
added no test of their own.
