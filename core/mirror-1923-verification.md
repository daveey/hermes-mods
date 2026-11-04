# Mirror 1923 verification

Frozen fork tip: `ea651aab330ef51dc3ad565c763c993a0e654e11`.
Upstream exclusion: `71736c9f32597bbca9d89cede5aa136e55946b2f`.
Merge-base: `a66e74dc67097219b6bb1807d18459859125be62`.

This pass appends five unpublished source SHAs as **1919–1923**, in reverse non-merge rev-list order. Published 0001–1918 patch bytes and ordinals are unchanged. The policy copies 1918 with only source-tip and exclusion-base updates; no substitution, fingerprint or rename rule changed.

## Replay bridges

The fourteen existing bridges and three ancestry-only entries are unchanged. Two new bridges carry upstream integration:

| Before | Bridge | Provenance |
|---|---|---|
| 1919 | `bridges/upstream-958df18a746.diff` | First-parent delta of upstream merge `958df18a746d8e2219367b9ddce0e185c7291dcd`, applied to the prior frozen tip. |
| 1922 | `bridges/upstream-ced18437aa5.diff` | First-parent delta of upstream merge `ced18437aa5e723d8e37a2a3e76a2742ba217192`, applied after 1919–1921. |

Each delta was applied with three-way Git application in source space, recorded from the resulting staged diff, and privacy-normalized with the policy substitutions. The source-space replay of the five new commits plus these two bridges had an empty full-tree diff against the frozen tip. No hand resolution was required. Both bridges are hash-pinned in `mirror-replay-1923.json`.

## Executed verifier

Using a shared bare clone of the fork checkout, this unchanged command exited **0**:

```sh
python3 -B core/verify_mirror.py --source-repo ../src.git \
  --source-tip ea651aab330ef51dc3ad565c763c993a0e654e11 \
  --base 71736c9f32597bbca9d89cede5aa136e55946b2f \
  --patch-dir core/patches --policy core/mirror-policy-1923.json \
  --replay-plan core/mirror-replay-1923.json
```

```json
{"ancestry_only": 3, "base": "71736c9f32597bbca9d89cede5aa136e55946b2f", "bridges": 16, "merge_base": "a66e74dc67097219b6bb1807d18459859125be62", "patches": 1923, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "ea651aab330ef51dc3ad565c763c993a0e654e11"}
```

This verifies contiguous ordinals, unique SHA headers, exact equality with the non-merge source set, the entire replay and normalized full-tree equivalence. The README Applying Bash recipe was separately extracted and executed unchanged: exit **0**. Its resulting commit `1dbc4aa57ef9d8cfc5f476cd0f62c242e5647447` also passed `verify_mirror.compare` under the same policy.

The index-object preflight checked 11,032 distinct nonzero IDs. Four historical abbreviated IDs were unavailable (`a3701890ce1`, `9ff9444f10`, `8274bdeb3db`, `43edb3e3f09`); the complete replay applied successfully without needing those fallback preimages. No verifier or published patch was changed to work around them.

## Tests

On the source-space replay tree identical to the frozen tip, the canonical `scripts/run_tests.sh -j 1` runner with a scratch `HERMES_HOME` returned **216 passed, 0 failed** across six files:

- `tests/agent/test_file_safety_credentials.py`: 24 passed.
- `tests/ci/test_fetch_merge_base.py`: 5 passed.
- `tests/hermes_cli/test_kanban_worker_repin.py`: 33 passed.
- `tests/tools/test_credential_files.py`: 66 passed.
- `tests/tools/test_docker_environment.py`: 84 passed.
- `tests/tools/test_kanban_creator_repin.py`: 4 passed.

Desktop typecheck/Vitest, a live Docker mount and a live Board repin were not rerun by this mirror pass. The feature docs distinguish fixture coverage from live integration.

## Privacy and coverage

The new patches use the standard path/repository substitutions and public `From:` identity. New bridges use the same substitutions. The per-file denylist scan, staged coverage check and pre-commit secret scan are publication gates; their final results are recorded in the task handoff.
