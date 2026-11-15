# Home-state placement and shell spelling guards

Patches: 3799, 3819, 3820, 3832.

## Behavior

Recognizes home-state directory changes and relative state paths without pinning unrelated repository-local `.hermes` paths after a later directory change. The read-root exemption cannot hide a `cd` or `..` using shell line continuations.

## How it works

Home-relative spans stop at the next `cd`/`pushd`/`popd`, except a change into the state directory itself. Direct changes into the home state directory pin too; repository/code subdirectories retain their portability rules. Bounding each span keeps repeated no-match home-directory changes linear. The command-ending lookahead accepts whitespace-separated numeric-fd redirects, but not a directory word with digits glued to it.

`_last_rel_state` locates `.hermes/` on the original matched text, not a lowercased copy whose Unicode length may differ. `_strip_read_paths` checks both its existing quote/backslash-stripped spelling and a shell-continuation spelling. Only an odd backslash run before LF joins a line; escaped pairs survive, and CR is not silently treated as a continuation. Either spelling exposing traversal or directory changes preserves the marker rather than clearing the declared read root.

## Code

- `hermes_cli/kanban_home_marker_placement.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_placement_coordination.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/hermes_cli/test_kanban_hub_read_roots.py` (patches 3799, 3819, 3820).
- `tests/hermes_cli/test_kanban_placement_home_spellings.py` (patch 3799).
- `tests/hermes_cli/test_kanban_pool_queue.py` (patch 3799).
- `tests/hermes_cli/test_kanban_state_path_home_forms.py` (patch 3832).

- **3799:** Home-spelling and pool tests distinguish local state paths/direct state-directory changes from repository-relative paths after later cd; own-worker artifacts retain a local marker; long no-match inputs satisfy bounded scan timings.
- **3819:** Added hub-read-root controls keep home placement for split-dot traversal, split cd and CR/newline forms rather than incorrectly clearing the root marker.
- **3820:** An escaped backslash before newline does not join commands and hide a later continued cd; the added control must still pin.
- **3832:** State-path home-form cases cover fd-redirected cd/pushd and Unicode text before the relative marker, asserting the queue class, normalized path and capability label.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

Placement classification is not filesystem authorization: the read tool still enforces its roots. The conservative spelling model is not a complete shell parser. Keep the mandatory historical hostname regex escape repair in the full replay recipe; these placement patches do not replace it.

## Plugin?

No. These changes affect core placement regexes, home-marker classification and declared-root coordination.
