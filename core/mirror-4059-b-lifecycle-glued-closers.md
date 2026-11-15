# Lifecycle guard follows consecutive shell group closers

Patches: 3816.

## Behavior

The lifecycle guard attributes trailing stdin redirection to all groups closed by a consecutive closer run, so a nested shell cannot hide lifecycle code from the scan.

## How it works

`_LineWalk.word` counts the already-closed run in `closing[1]`. A matching next closer is accepted while the segment contains only that run; it routes the previous group's output into the enclosing group, pops the next group and increments the run length. Trailing redirection is consequently associated with the outermost closed group rather than the innermost one. Mixed brace/if/loop/case closers and piped nested output share this path.

## Code

- `cron/lifecycle_guard_groups.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/cron/test_lifecycle_guard_group_redirections.py` (patch 3816).

- **3816:** Parameterized scan cases cover consecutive braces and mixed if/loop/case closers, nested output pipelines and Python stdin; clean later commands, quoted/data uses and unrelated redirects remain allowed.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

The fix does not make every adjacent closer executable syntax: the expected group closer must match. Redirection after a closed run must not leak into a later clean shell, and literal echo/data uses remain allowed. This is command analysis, not a shell sandbox.

## Plugin?

No. The core lifecycle command walk must maintain group/feed state consistently.
