# Bounded streaming of large FTS integrity checks

Mirror entries: 5076, 5077, 5078.

## Behavior

- Online search-recovery planning can judge integrity output past the former 100,000-line limit without building a second list of problem lines.
- Empty output, incomplete/capped walks and structural damage outside search indexes still refuse recovery.
- The check limit is 5,000,000 lines; this is bounded processing, not unbounded recovery permission.

## How it works

`plan_from_check()` accepts an iterable and retains counts, damaged families and refusal information rather than every problem line. `plan_online_recovery()` lazily iterates newline-separated messages within each SQLite row inside the snapshot. `integrity_line_is_structural()` shares the existing structural classification with non-streaming callers.

The planner ignores `ok` and database marker lines when counting problems, conservatively refuses at `limit - 1` problem lines, and refuses a check that returned no lines. An empty check may indicate SQLite text-output overflow; it is never interpreted as a clean store. Follow-ups clarify memory accounting and rewrap source lines.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_state_fts_online.py`
- `hermes_state_repair.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Online-recovery tests cover a generated walk beyond the old cap, canonical damage after many index lines, empty output, marker handling, limit boundaries, the cap versus SQLite maximum length, lazy iteration and a multiline single-row walk. Free-gate fixtures are updated. Real oversized SQLite database repair, CLI repair-search and live store recovery are untested here.

Source test/coverage paths:

- `tests/hermes_state/test_fts_free_gates.py`
- `tests/hermes_state/test_fts_online_recovery.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

SQLite still constructs a potentially large row and Python decodes that text; streaming avoids an additional split-line list, not all memory costs. Existing read-only/quarantine/layout and recovery safety gates remain in force. Raising the cap must not turn a truncated check into success.

## Why core instead of a plugin

Safe recovery depends on the core SQLite snapshot, index-family mapping and structural-damage gates. A plugin cannot substitute an out-of-band partial integrity report for those invariants.
