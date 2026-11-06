# Mirror 2016 verification

Snapshot: 2016 patches through source tip
`d9484acb9d3158bad78466793541023278597149` (fork PR #907, rebase-merged).
Upstream exclusion tip `84a0301415edd6d1385207ec96e4d7f2b6c1fcd9` and merge-base
`c55af2c93b9d61f5db7943fbc5ff50553038171f` are unchanged from snapshot 2015.

## Inventory

`2a6c4ae61b9c13e847cfb320a3e609fcc32ea5f8..d9484acb9d3158bad78466793541023278597149`
contains exactly one non-merge commit and no merges, so patch 2016 is appended
with no new bridge. `mirror-policy-2016.json` and `mirror-replay-2016.json` are
the 2015 files with only their `source_tip` changed. All 2015 published patch
bytes and all twenty-three bridges are unchanged.

Patch 2016 was exported with `git format-patch -q -1 <sha> --start-number 2016`
and scrubbed with the standard substitutions; the only resulting change was the
`From:` author line. The private denylist reports 0 matches in the new patch.

## Executed checks

Full unchanged verifier against a `--bare --shared` clone of the hub's live fork
checkout, with the 2016 policy and replay plan, exit 0:

```json
{"ancestry_only": 3, "base": "84a0301415edd6d1385207ec96e4d7f2b6c1fcd9", "bridges": 23, "merge_base": "c55af2c93b9d61f5db7943fbc5ff50553038171f", "patches": 2016, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "d9484acb9d3158bad78466793541023278597149"}
```

The README Applying recipe (extracted verbatim from this README's Bash block,
including its embedded verifier run) exited 0. Its final commit is patch 2016.
All five files patch 2016 changes are byte-identical to the source tip in that
replayed tree. Remaining raw differences are the policy's reviewed public
normalizations, which the verifier compares.

Tests on both trees (`scripts/run_tests.sh -j 1 --file-retries 0`):
`tests/hermes_cli/test_kanban_wait_replica_unknown_scope.py`,
`tests/hermes_cli/test_kanban_offline_backoff.py`,
`tests/hermes_cli/test_kanban_wait_replica.py`: 72 passed, 0 failed on the
replayed public tree, and 72 passed, 0 failed on the source tree.

## Not covered

No Desktop, TUI or full-suite run in this pass. The source PR's CI and
independent review are the evidence for the wider suite.
