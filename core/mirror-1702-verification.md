# Mirror 1702 verification

Frozen source: `871e0a9e2161b7e58fd10e25b8d7322dd409bd97`.
Upstream exclusion: `f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab`.
This pass appends **77** previously unpublished non-merge commits as **1626–1702**,
including the buffered held-draft notice (1701) and MCP finalization recovery guard (1702).
The published 0001–1625 patch files and their ordinals remain unchanged.
Source commits landing after this frozen tip belong to the next snapshot.

## Executed acceptance

The verifier exited **0** on the complete inventory and normalized full-tree comparison:

```sh
python3 -B core/verify_mirror.py --source-repo ../src.git \
  --source-tip 871e0a9e2161b7e58fd10e25b8d7322dd409bd97 \
  --base f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab \
  --patch-dir core/patches --policy core/mirror-policy-1702.json \
  --replay-plan core/mirror-replay-1702.json
```

```json
{"ancestry_only": 3, "base": "f0d94882e89ab8d4098b5bb2f166adfb9ee0a0ab", "bridges": 7, "merge_base": "e8639fd4312e5102d6f10c76b56406cb01c19092", "patches": 1702, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "871e0a9e2161b7e58fd10e25b8d7322dd409bd97"}
```

The source object store is a shared bare clone of the fork checkout. Preflight checked
9,673 nonzero historical patch index IDs. Four historical abbreviated IDs were unavailable
(`43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`, `a3701890ce1`); none was needed by a three-way
fallback during the successful complete replay. No historical patch was regenerated to hide this.

## Source merges, not duplicate deletion

The seventh hash-pinned bridge, `mirror-upstream-1702.diff`, is the normalized combined
first-parent delta from `b0a735b7cde^1` through `fcef5de8d61`. These two upstream merges
reached main via `622f8967b06`. It applies immediately before 1627 and preserves the six
previous bridges. README's Applying recipe includes the actual new boundary.

Three source commits are retained by merges that deliberately kept their first-parent trees:

| Ordinal | Source commit | Ancestry-only source merge |
|---|---|---|
| 1626 | `7d7c542ce763a215594ecaee72905438736af889` | `0013a0897e0b460762251c4a522cb5edef5e04bd` |
| 1649 | `34fe22e1f16f1244714d570e3357a9591df3e3aa` | `ccbe7c69d0b57102987f41fb021b68f68363843e` |
| 1656 | `243054ddac01f2ef9ff3f4fe20bd321fa4fb36d2` | `ccbe7c69d0b57102987f41fb021b68f68363843e` |

Their full mail patch bodies are preserved, not replaced with empty entries. The new optional
`ancestry_only` replay-plan field requires a single-parent source patch, a real merge reachable
from the frozen tip, exact merge/first-parent tree equality, and patch reachability through the
merge but not its first parent. Duplicate entries, unknown headers and bridge-boundary overlap
are rejected. Each such patch is applied once in a separate throwaway worktree at its source
parent. Its entire resulting tree must match the original commit with only the standard privacy
substitutions: no fingerprint or rename exemptions. The main replay records an explicit empty
ancestry-only marker, matching the source merge's decision. Both worktrees are cleaned on errors.
The full inventory and final-tree comparisons remain mandatory and unchanged.

1626 is **not** byte-identical to the later port 1653, so treating it as an empty duplicate would
be wrong. The full source diffs of 1649/1657 and 1656/1658 are respectively identical; retaining
all four actual patch bodies and following the source merge avoids inventing a duplicate policy.

### Why the initial linear replay failed

1. `git am -3` conflicted at 1626 in `kanban_db_dispatch_pr_state.py`.
2. Its source parent predates the published mainline's provenance/handoff changes; the later
   source merge `0013a0897e0` explicitly retained the reviewed first-parent port instead.
3. A linear non-merge patch list cannot represent that source merge decision. An upstream bridge
   cannot express it either: the old bridge schema requires its merge to precede the next patch,
   whereas this merge occurs after the old branch commit.
4. The existing full replay gate caught the mismatch before publication. The missing representation
   was in the replay plan, not an excuse to loosen the source inventory or tree equality checks.

Countermeasure: source-checked side replay plus an explicit merge marker. The new test suite covers
successful side replay, substitution-only normalization, malformed/duplicate entries, false merge
claims, patch-body tampering, apply failure and cleanup. Original verifier tests remain intact.

## Reviewed normalization re-pins

Four files already had explicit reviewed fingerprints and changed in this batch. Old and new
source/applied diffs were inspected; the added/removed lines are byte-identical (one hunk's line
number shifted in `kanban_remote_notices.py`). No new fingerprint, rename or global substitution
was added:

| Path | New source blob | New applied blob |
|---|---|---|
| `hermes_cli/kanban_placement.py` | `f610ced035d5a125401ab36c32b6fe1102105a67` | `4c6402c8ef88a2d4a921ec8d0df6604138412ca6` |
| `tests/hermes_cli/test_kanban_placement.py` | `04b19025ec836ada87189a470395fc26d7e87986` | `055460625f6af4a49e86ccd68a6bc55703fbf6a0` |
| `tests/hermes_cli/test_kanban_sync_idle_round.py` | `3a0033c1325c3693a1a6528d0aa8c596f8a8a729` | `b9385f170ea5869c7f8e4e7fe76ba0d3ff51e7a4` |
| `tui_gateway/kanban_remote_notices.py` | `42198de55139883a2a000f1f0bdb42bc90061d07` | `2c46fe8cad48b7bf9d04915e9fe3de509c24907d` |

## Additional executed checks

- `python3 -B -m unittest discover -s core -p 'test_verify_mirror*.py'`: **35 tests, OK**.
  The ancestry-only tests were first run against the old verifier and failed before the fix.
- Independent adversarial review found no weakening of acceptance. Its extra-mail observation
  was reproduced in both main and side replay: two tests failed because extra empty mbox
  messages were accepted. Inventory now requires exactly one unescaped mail separator per
  file; both tests pass. This closes a pre-existing gap where add/revert or empty messages
  could preserve the final tree while violating one-entry-per-source accounting.
- `core/check_coverage.py`: **exit 0**; all new ordinals have feature-doc rows.
- The exact README Bash Applying recipe ran **exit 0**, and its resulting full tree passed the
  same policy comparison. Its throwaway worktree was removed.
- Private denylist: **0 hits per file** across all 126 new/changed core files, including the bridge.
  Mail descriptions additionally replace private incident identifiers and local home examples;
  operational protocol literals in diffs remain unchanged.
- Normal pre-commit gate: **exit 0**, including gitleaks, denylist and coverage. Documentation,
  Python and JSON whitespace checks pass; patch/bridge context whitespace is preserved.
- All 0001–1625 published patch bytes are unchanged. New SHA headers equal the frozen missing
  list, in the required reverse-rev-list order. No source runtime patch was emptied.

## Feature and runtime evidence limits

README's 1626–1702 table indexes every new ordinal to a feature doc, including the extended
[pre-final-gate.md](pre-final-gate.md). Feature docs cite source tests and explicitly distinguish
untested paths. This mirror pass does **not** rerun all runtime tests or certify live deployment.
The mirror verifies the source snapshot's publication fidelity, not every feature's runtime behavior.
