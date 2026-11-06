# Mirror 2017 verification

Snapshot: 2017 patches through source tip `a9aa1aa4e4aeca6c6dba90b84f3446e825d999bb` (fork PR #908).
Upstream exclusion tip `84a0301415edd6d1385207ec96e4d7f2b6c1fcd9` and merge-base `c55af2c93b9d61f5db7943fbc5ff50553038171f` are unchanged from snapshot 2016.

## Inventory and preservation

Exactly one non-merge commit follows snapshot 2016; no intervening merges. Patch 2017 is that commit, identified by its SHA header. All 2016 previously published patches and all twenty-three bridges are unchanged. Policy and replay-plan copies change only their `source_tip`; no verifier changes or new exceptions.

Patch 2017 was exported with `git format-patch -1 --start-number 2017`, normalized using existing literal/regex substitutions and the public author line. The private denylist scan returned zero matches.

Preflight checked 11538 distinct nonzero patch-index IDs. Four historical abbreviated IDs were unavailable: `43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`, `a3701890ce1`. Neither full replay required them; both completed without any object repair or verification relaxation.

## Executed verification

The unchanged full verifier ran against a shared bare source clone as the first step of the verbatim README Bash recipe. Exit 0:

```json
{"ancestry_only": 3, "base": "84a0301415edd6d1385207ec96e4d7f2b6c1fcd9", "bridges": 23, "merge_base": "c55af2c93b9d61f5db7943fbc5ff50553038171f", "patches": 2017, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "a9aa1aa4e4aeca6c6dba90b84f3446e825d999bb"}
```

The remainder of the README Applying recipe also exited 0 and ended at patch 2017. Its independently replayed tree passed the unchanged verifier's `load_policy` and `compare` full-tree comparison. The changed test file is byte-identical to the source tip.

Canonical runner on each tree:

```sh
HERMES_PYTHON=<existing-test-python> bash scripts/run_tests.sh -j 1 --file-retries 0 \
  tests/hermes_cli/test_kanban_wait_replica_unknown_scope.py \
  tests/hermes_cli/test_kanban_offline_backoff.py \
  tests/hermes_cli/test_kanban_wait_replica.py
```

Source: 75 passed, 0 failed. Replayed public tree: 75 passed, 0 failed. Both used the same existing test interpreter read-only; no runtime dependencies were modified.

## Limits

No full-suite, Desktop, TUI or live-gateway test was run in this mirror pass. No live database edits or forced runtime updates were performed.
