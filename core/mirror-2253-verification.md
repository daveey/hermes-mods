# Mirror snapshot 2253: inventory, replay and normalization

## Scope

This append preserves published entries **0001–2156** and adds **2157–2253**: 97 non-merge source commits through `9bc6ed6b6df403e0c80529753b9814d3ec119cba`. The upstream exclusion remains `8471f614b4879144d36e8919fb4e1f3c430d2100`; merge-base remains `b3746e35ddd32079697728e2df57a9d78f1e9e32`. There are no new merge commits in this interval. All 41 existing bridges and three ancestry-only entries remain unchanged.

The append includes exact-question early answers (2215) and historical completion receipts (2244–2253). The former extends the existing [clarify document](clarify-early-answer.md), preserving its published plugin additions. The latter is documented in [completion receipts](completion-receipts-2253.md). All new ordinals have feature-document coverage in the README.

## Executed checks

- Independently regenerated all 97 source patches: normalized content matches, with only Git's cosmetic index-ID abbreviation lengths differing. Contributor-path normalization is the explicit exception below. Every SHA header agrees with its source commit; no empty-mail substitution was introduced.
- Published patch and bridge comparison: **2196 files byte-identical** to the prior public revision (the separately stored upstream bridge is also unchanged in Git).
- Private denylist scan: **138 new patch/document/policy files, zero matches**; normal staged pre-commit checks also passed before publication.
- `python3 -B -m unittest discover -s core -p 'test_*.py' -v`: **42 tests passed**. These exercise inventory, coverage, replay policy and ancestry-only verification—not the mirrored feature suites.
- Executed the README's actual Bash apply slices through ordinal 2253 in a separate disposable tree, then ran the unchanged policy comparison: **`README_RECIPE_TREE_PASS`**. The tree was removed afterwards.
- Index preflight checked **12758 distinct nonzero patch index IDs**. Three historical abbreviated objects were unavailable (`43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`). Complete replay succeeded without needing those obsolete preimages; no patches or checks were weakened to compensate.

## Final verifier result

The unchanged full verifier exited **0** with the frozen policy and replay plan:

```json
{"ancestry_only": 3, "base": "8471f614b4879144d36e8919fb4e1f3c430d2100", "bridges": 41, "merge_base": "b3746e35ddd32079697728e2df57a9d78f1e9e32", "patches": 2253, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "9bc6ed6b6df403e0c80529753b9814d3ec119cba"}
```

Policy SHA-256: `67441853d87a0336990e9677e7b48765bc5d44aac41546eb90fde8c1000859b9`.
Replay-plan SHA-256: `ed8b9a8417038bf681afbbf2b1def210bf994e63ec9d8171f04fea3814470d69`.

## Privacy policy and independent review

The new policy retains all existing substitutions, renames and fingerprint entries. It adds three snapshot-bound regex rules: the fork-organization repository family maps to `example-org/hermes`, the organization family maps to `example-org`, and the project-tool family maps to `example-tools`. Independent inspection of the frozen source found only the intended organization/project matches. These patterns must be reviewed again for future snapshots, not treated as a general permission to mask code differences.

A broader capitalized `-AI` rule was rejected during review because it would normalize unrelated third-party names. The historical organization mention in new mail-description prose was anonymized without adding a source-tree normalization rule.

The new contributor path is `contributors/emails/build-worker@example.com`, with identical source/applied content blobs. Seven existing fingerprint pairs were re-pinned after source changes: placement, wait checks, watch parks, update pauses, remote notices, notice-guarantee tests and runner-recovery tests. Each residual normalized diff has exactly the same removed/added lines as its previously reviewed 2156 counterpart. The runner-recovery applied blob additionally receives the new deterministic repository substitution before this comparison. No new fingerprint permits unexplained functional divergence.

Independent review approved the policy/repins and source-patch integrity. Its test-name correction and overbroad-rule finding were fixed before publication. Published patches, historical policies, bridges and the verifier itself were not edited.

## Limits and inherited issue

This is a source-mirror proof, not deployment or end-to-end feature certification. The feature documents identify source-supplied tests and gaps; those feature suites were not rerun by this mirror pass. Runtime deployment and live decision/receipt acceptance remain with their feature owners.

An independent review also found an **inherited** hostname-normalization defect: the existing applied placement marker `r"\mini-1\b"` fails `re.compile` with `bad escape \m`. It is unchanged from the published 2156 snapshot and retained here rather than silently rewriting immutable patches. A separate repair task owns the normalization guard and reviewed repair. Consequently, tree equivalence under the historical policy must not be represented as proof that every inherited public-tree runtime path works.

## Reproduction

Use the full command and apply recipe in [README.md](README.md#applying), with `mirror-policy-2253.json` and `mirror-replay-2253.json`. Both bind the exact frozen source tip. A successful apply without inventory and whole-tree comparison is not acceptance.
