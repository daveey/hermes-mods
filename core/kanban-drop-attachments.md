# Drag files onto Kanban tasks to attach them

Patch: `0057`

## Behavior

- Drag one or more files from Finder (or anywhere in the OS) onto a **task card** on the Kanban board: the card
  shows a dashed "Drop to attach" overlay, and dropping uploads every file to that task. A toast confirms
  "Attached N files to “title”"; errors (e.g. the backend's per-file size cap) toast too.
- Drag files onto an open **task dialog** (anywhere on it): the same overlay covers the dialog, and the files land
  in its Attachments section.
- Dragging a card between lanes is unchanged. Lanes ignore file drags, so a file dropped between cards does nothing.

## How it works

Card moves set `text/plain` (the task id); OS file drags carry the `Files` type. `isFileDrag()` tells them apart,
so the two drags never collide. `useFileDrop()` returns drag handlers + an `over` flag: enter/leave are counted
(no flicker crossing child elements), and file events `stopPropagation()` so the enclosing lane never sees them.
Uploads go one file at a time through the existing `POST /tasks/{id}/attachments` endpoint (`uploadFiles()`), so
the backend's name sanitising and size limit still apply. No backend change.

## Code

- `apps/desktop/src/plugins/kanban/attach-drop.ts`: `isFileDrag`, `uploadFiles`, `useFileDrop`.
- `board.tsx` `Card`: upload mutation + overlay; `Column` drag handlers bail out on file drags.
- `drawer.tsx` `TaskDrawer`: the `DialogContent` takes the drop handlers; overlay via its `chrome` slot. Only
  enabled when the backend returns an `attachments` list (older backends have no upload route). The upload
  button now calls the same `uploadFiles`.
- `i18n.ts`: `dropToAttach`, `attachedFiles(n, title)` in en/ja/zh/zh-Hant.

## Tests

`npx vitest run src/plugins/kanban` — `attach-drop.test.tsx` (file vs. card drags, highlight, no bubbling to the
lane, disabled) and `drawer.test.tsx` ("attaches files dropped anywhere on the task dialog").

## Applying

`git am -3 --empty=keep 0057-*.patch`. Independent of the other patches.

## Gotchas

- Is it a plugin? No: the Kanban board is a bundled desktop plugin compiled into the renderer, with no extension
  point on cards.
- A drop during an in-flight upload on the same card is ignored (the zone is disabled while pending).
