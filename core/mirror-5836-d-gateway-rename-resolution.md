# Exact gateway aliases and partition-preserving rename resolution

Source patch ordinals: 5778–5780.

## Behavior

Local origin, wait and overlap resolution can follow an in-place gateway rename. Explicitly present partitions remain authoritative; alias direction alone never overrides them. Reply/comment matching recognizes exact supported gateway identities, not sibling-name substrings.

## How it works

`kanban_gateway_alias` combines `kanban.placement.aliases` with locally known partition evidence. An absent name can fall back only to a unique known equivalent. Origin identity refresh avoids memoizing half-written renames. Comment matching accepts exact `origin_gateway` or supported author components, including gateway-chat and relay/card forms. Local wait resolution uses aliases while an explicitly typed SSH route remains the requested host.

## Code

- `hermes_cli/kanban_gateway_alias.py`
- `hermes_cli/kanban_origin.py`
- `hermes_cli/kanban_comment_origin.py`
- `hermes_cli/kanban_wait_check.py`
- `hermes_cli/kanban_overlap.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/hermes_cli/test_kanban_gateway_rename.py`, `test_kanban_gateway_rename_overlap.py` and `test_kanban_sync_gateway_rename.py` cover hot rename detection, alias chains/loops/case, delayed config/feed order, explicit and tombstoned partitions, old references, local CLI wait/comment matching, sibling negatives, unchanged SSH routing and sync adoption/publication.

## Applying

Apply the alias helper and all origin/comment/wait consumers together. Configure aliases as operational equivalence; preserve explicit partition evidence during cutover.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

This is local resolution, not network discovery or authority to merge arbitrary partitions. An ambiguous absent name stays unresolved. Generic examples must not replace protocol author formats such as `gw:`.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
