# Mirror 1539 verification

Frozen source: `37c70bbcaa1c0ce3d960ba0bd71adf237dbdf7e9`. Upstream exclusion:
`c7da41dd522dc19cc08bbfcfa69d4b078224b45d`. This pass appends **44** previously unpublished
source commits as **1496–1539**. All 0001–1495 patch bytes and ordinals are unchanged.
Commits landing after the frozen tip belong to the next pass.

## Executed acceptance

The unchanged verifier exited **0** after replaying the entire series and comparing the full
resulting tree against the frozen source under `mirror-policy-1539.json`:

```sh
python3 -B core/verify_mirror.py --source-repo ../src.git \
  --source-tip 37c70bbcaa1c0ce3d960ba0bd71adf237dbdf7e9 \
  --base c7da41dd522dc19cc08bbfcfa69d4b078224b45d \
  --patch-dir core/patches --policy core/mirror-policy-1539.json \
  --replay-plan core/mirror-replay-1539.json
```

```json
{"base": "c7da41dd522dc19cc08bbfcfa69d4b078224b45d", "bridges": 5, "merge_base": "c627ec84664d43e0b18b95d9d86d8963747d56c7", "patches": 1539, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "37c70bbcaa1c0ce3d960ba0bd71adf237dbdf7e9"}
```

`core/check_coverage.py` also exited 0 on the staged series. This checks README/doc coverage;
the full verifier additionally checks contiguous ordinals, unique source headers, exact source
commit-set equality, replay and final tree equivalence. No verifier logic was changed.

## Replay adaptations and failure analysis

The first full replay stopped at the second copy of the macOS CI add-file change. Source commits
1503/1516, 1504/1517 and 1505/1518 have byte-identical diffs (distinct source SHAs and parents).
The second add-file patch encountered files already changed by the encoding/runner follow-ups.
Why: a linear export repeats changes that Git's branch merge coalesces. Matching SHA headers alone
does not detect semantically duplicate diffs. The replay gate caught this before publication.

The unpublished entries **1516–1518** retain their original SHA headers and messages as explicit
empty mail entries, applied with `--empty=keep`; their changes are represented by 1503–1505
(with the privacy normalization below). No source commit or ordinal is skipped.

After that correction, tree comparison detected missing upstream files. Why: the inherited
four-bridge plan predates upstream merge `bbb69887a1c`; non-merge fork inventory does not carry
upstream merge changes. The source merge's exact first-parent diff, under the standard three
substitutions, is now `bridges/upstream-bbb69887a1c.diff`, hash-pinned before **1523** in the
replay plan. The README Applying recipe includes all five bridges and the complete final segment.
This preserves published patches rather than rewriting historical context. Inventory-only or
apply-only checks would miss the omitted merge content; the unchanged full-tree gate caught it.

## Privacy and policy review

All new patches use the public author address and standard home-path/project substitutions.
The private denylist scan found zero hits in the staged files and separately in the new bridge.
Independent review confirmed the bridge is byte-identical to its normalized source merge diff.

Seven fingerprint entries were reviewed (four new, three updated):

- `hermes_cli/kanban_wait_check.py`, `tui_gateway/kanban_remote_notices.py`, and
  `tui_gateway/kanban_unloaded.py`: re-pin existing reviewed IP/comment-identifier normalization
  after source changes; no new runtime semantics.
- `hermes_cli/kanban_overlap.py`: comments only, replacing audit identifiers/timestamps and
  personal wording; operational logic unchanged.
- `tests/tools/kanban_overlap_role_cases.json` and `test_kanban_create_overlap_roles.py`:
  explicitly labelled synthetic public equivalents replace private live task bodies, task IDs,
  employer project references and source provenance. Assertions and refusal/admission semantics
  are retained; these are not represented as original incident inputs.
- `tests/ci/test_recover_macos_runner.py`: a private credential-scope reference in a docstring
  becomes generic credential wording.

The policy retains all other historical fingerprints and rename rules unchanged. Full comparison
requires every fingerprint to match both exact blobs and be used; unexplained differences fail.
Two independent review passes examined feature/doc accuracy and the replay/policy adaptations.
The reviewers additionally reported 208 overlap-related tests passing and 109 overlap/CI-fixture
tests passing, with matching source/public results; those are reviewer-run targeted checks, not a
claim that every feature suite was rerun by this mirror pass. Runtime, Desktop and live-provider
acceptance remain with the source implementation owners.

## Notable feature links

- 1529 (`dee20bcec75f`): [turn-reset-partial-import.md](turn-reset-partial-import.md).
- 1534 (`7adafaf5765f`): [kanban-shared-blockers.md](kanban-shared-blockers.md).
- 1539 (`37c70bbcaa1c`): [mirror-runtime-hardening-1302.md](mirror-runtime-hardening-1302.md).

Every appended ordinal has a README row and a new or extended feature document. Applying one
feature patch alone is not certified; use the full ordered replay recipe.
