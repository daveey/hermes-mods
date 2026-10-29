# Public mirror verification: 390 ordered patches

## Exact snapshot

- Core source: `3d318c51379635168cdad6c8e36c345e6d5b6a86`.
- Upstream reference: `edfa1c2a135532a8b64f7825c0ac0af6cfe73d4d`.
- Merge base: `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`.
- Applied public tree: `1ccc561cbb104ede045978b8ad397231f2d6c207`.

The full non-merge source range was enumerated and exported. All 384 previously reviewed public patch bodies remain byte-identical, including privacy substitutions, explicit empty duplicate envelopes and merge adaptations documented in [the earlier verification](mirror-377-verification.md). The historical 0251 adaptation retains its replacement envelope identity rather than being overwritten by its raw source commit. This is a public adapted series, not a claim of tree identity with private source.

Six new patches were added: adoption **0385, 0386, 0388** and annotation/alias-read latency fixes **0395, 0396, 0397**. Previous archive-refresh 0385 is now **0389**; local-authority 0386, 0388, 0389, 0390 is now **0390, 0391, 0393, 0394**. Documentation references moved with the patches. No hotkey changed.

## Full apply validation

In a fresh isolated clone, checked out at the merge base:

```sh
git am -3 --empty=keep /path/to/hermes-mods/core/patches/*.patch
```

Exit 0; all **390 patches** produced **390 commits**, with a clean applied worktree. Numbering is contiguous, and every previous patch was compared byte-for-byte by envelope identity. All **464** existing changed Python files in the applied tree parse successfully.

## Tests executed on the applied public series

The canonical per-file runner, `bash scripts/run_tests.sh -j 3`, passed **69 tests across eight files**, zero failures:

| File under `tests/hermes_cli/` | Passed |
|---|---:|
| `test_kanban_origin_adoption.py` | 12 |
| `test_kanban_origin_owner.py` | 33 |
| `test_kanban_annotation_snapshot.py` | 1 |
| `test_kanban_local_dedup_cost.py` | 1 |
| `test_kanban_local_origin_projection.py` | 9 |
| `test_kanban_local_receipt_authority.py` | 3 |
| `test_kanban_remote_identity.py` | 9 |
| `test_kanban_origin_projection_contention.py` | 1 |

The checkout's automatic PM setup was unavailable (packaged PM runtime missing/invalid). Tests therefore used the runner's supported `HERMES_PYTHON` override with an isolated Python 3.11.17 test environment and explicitly installed test dependencies. This proves the selected regressions on the applied series, not a locked-environment/full-suite certification. No new Desktop suite, typecheck, live API timing or runtime activation was performed.

## Coverage and privacy

- `python3 -B core/check_coverage.py`: pass; all 390 patches have feature-doc coverage.
- Full private denylist scan: all 390 patch files, zero matches.
- `gitleaks dir core --no-banner --redact -c .gitleaks.toml`: no leaks found.
- Documentation `git diff --check`: pass. Mail patch context/trailer whitespace is preserved, not normalized.
- Local commit uses normal repository staged privacy and coverage hooks, without bypasses.

Only the isolated public mirror was changed. No source checkout edits, runtime actions, legacy migration, board transitions or push are part of this verification.
