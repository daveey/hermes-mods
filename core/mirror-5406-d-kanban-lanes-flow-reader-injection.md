# Kanban lanes flow: optional reader injection

Mirror snapshot 5406; ordinals 5403.

Entries:

- **5403**: Allow nonblocking published PR readers in lanes flow

## Behavior

- `kanban_lanes_flow.snapshot` gains an optional keyword `reader`. When a caller injects an object exposing `prefetch(urls)` and `get(url)`, the snapshot prefetches PR URLs through it and classifies from its answers; no network `Reader` is constructed.
- Default (`reader=None`): the CLI path still builds a fresh, bounded `Reader` and takes network observations on every report. Classifier policy is untouched.
- **Core owns only the seam.** Core has no background worker lifetime. Whoever injects a non-blocking reader owns its thread, cache, invalidation and shutdown; for a dashboard that is the plugin.

## How it works

- `snapshot()` imports and constructs `Reader` only when `reader is None`; then `reader.prefetch(_pr_urls(conn, rows))` as before.
- Duck-typed: any object with `prefetch`/`get` works.
- The test injects a fake `Published` reader, forbids `Reader` construction and asserts classification of an approved, green, unmerged PR as the merge step.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_lanes_flow.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/hermes_cli/test_kanban_lanes_flow_evidence.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- A reader that blocks defeats the purpose.
- Stale answers are the reader owner's responsibility.
- Producer-reported evidence (not mirror execution): a RED run failed with an unexpected `reader` keyword before implementation; flow, PR observation and persisted-evidence suites passed 84 tests and an independent run repeated 84; both required CI checks were reported green.

## Why core instead of a plugin

A reader-lifetime worker in core would add a process-wide thread nobody can stop cleanly; the seam in core lets the dashboard plugin own that worker. The classification logic itself is in core, which is why the keyword lives there.
