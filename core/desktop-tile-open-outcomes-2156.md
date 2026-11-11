# Tile-open outcomes and cache-paint evidence

Patches **2132–2133**.

## Behavior

Tile opens record failures instead of silently disappearing from open-timing diagnostics. A resume response without a runtime session ID records `outcome: "failed"` before throwing. A cold tile open saves its REST transcript page under the tile owner's cache scope for later opens.

A sidebar click that only fronts an existing tile is not a main-pane open and must not produce a phantom abandoned-open record. Session-reference chip routing remains tile-based; this batch adds regression coverage rather than changing that routing.

## How it works

`failTileOpen` records a failed reconciliation and rethrows owner/resume errors. A tile rebind that loses to another binding records `abandoned`. `settleTileOpen` saves nonempty REST messages through `saveTranscriptTail`. Main-pane timing tracks which clicks actually entered resume before logging abandonment.

`isTileCachePaint` accepts only `surface: "tile"`, `paint: "cache"`, and `outcome: "rest"` together. `tileCachePaintProof` parses timing lines and matches the requested session ID. Main-pane records and failed/abandoned tiles cannot establish this proof.

## Code

- `apps/desktop/src/app/contrib/hooks/tile-cache-paint.ts`: failure handling and REST cache warming.
- `apps/desktop/src/app/contrib/hooks/use-session-tile-delegate.ts`: error, empty-runtime and rebind outcomes.
- `apps/desktop/src/lib/open-timing.ts`: entered-click tracking and proof helpers.

## Tests

Source tests inspected; **not executed in this mirror pass**:

- `apps/desktop/src/app/chat/session-ref-open-surface.test.tsx`: `a chip click opens a tile owned by the key the chip prefetched, and writes no main-pane click record`; `a sidebar click on a chat already open as a tile fronts the tile and logs no abandoned main-pane open`.
- `apps/desktop/src/app/contrib/hooks/use-session-tile-delegate.cache-paint.test.ts`: `a tile open that fails writes outcome "failed" instead of nothing`; `a resume that returns no runtime id writes outcome "failed" instead of nothing`; `a cold tile open caches the REST page it painted, under the tile owner key`.
- `apps/desktop/src/lib/open-timing.test.ts`: `a click that entered resumeSession and is superseded is still logged abandoned`; `main-pane records cannot satisfy the tile proof, whatever they painted`; `abandoned, failed, never-painted and REST-painted tile opens are not success`.

No live Desktop cache-paint proof was collected here. The losing-rebind outcome is visible in the implementation, but no dedicated new race test is claimed.

## Applying

Use the [core README full applying recipe](README.md#applying), including its pinned replay plan and integration bridges. These patches are not standalone; do not apply only these ordinals to an arbitrary upstream checkout.

## Gotchas

A cached first paint is not authoritative reconciliation. Errors are still rethrown. A timing record without the tile surface discriminator is not evidence about a reference-chip tile.

## Could this be a plugin?

A plugin could consume diagnostic lines, but cannot reliably repair built-in resume error handling, cache writes or sidebar timing ownership without core hooks.
