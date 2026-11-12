# Preserve an upstream contributor author alias

Mirror entries: [2271](patches/2271-fix-contributors-map-preserved-upstream-tile-pin-author-daveey.patch).

## Behavior

- Map the preserved upstream tile-pin author's commit email to the contributor handle used by the repository's contributor tooling.

## How it works

2271 adds one contributors/emails mapping entry and an upstream attribution comment. It changes no agent, UI or scheduling code.

## Code

- `contributors/emails/sahil9ss@gmail.com`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

No tests are added or changed by this mapping-only entry.

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

No new source regression is added by this mapping-only patch. The diff is the evidence for the alias; contributor generation and author identity were not independently executed or verified by this documentation pass.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.
