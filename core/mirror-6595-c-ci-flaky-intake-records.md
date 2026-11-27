# CI flaky-test intake supplement records

**Scope:** Documentation-only append of immutable CI occurrence records and dispositions under the existing intake document.

**Source ordinals:** 6452, 6505, 6506, 6507, 6529

## Behavior

- Append-only JSON supplements preserve unchanged observation records (run/job/head identities and exact test counts) from several intake batches, plus a Markdown index update.
- Original batch dispositions and source qualifiers are preserved verbatim; later annotations are added separately rather than rewriting pending dispositions.

## How it works

- Each supplement is a standalone JSON export whose name encodes the occurrence range, referenced from the single existing intake Markdown document.
- Page payload digests and rendered counts are carried over unchanged so earlier exports remain byte-identical prefixes.

## Code

Files touched by the series (paths relative to the source tree):

- `docs/ci-flaky-intake-2026-11-25-supplement-827-890.json`
- `docs/ci-flaky-intake-2026-11-25-supplement-891-895.json`
- `docs/ci-flaky-intake-2026-11-25-supplement-896-902.json`
- `docs/ci-flaky-intake-2026-11-25-supplement-903.json`
- `docs/ci-flaky-intake-2026-11-25-supplement-904-916.json`
- `docs/ci-flaky-intake.md`

## Tests

Source test files changed or added by the series:

- (none)

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6452-6529, 5 commits: 6452, 6505, 6506, 6507, 6529) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- No runtime code changes. The records describe source-side CI observations; this mirror does not re-validate them.
- Occurrence qualifiers keep unknowns explicit (for example recurrence or cause unknown, repair not delivered); this doc does not upgrade them.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

These are repository documentation files with no plugin surface.
