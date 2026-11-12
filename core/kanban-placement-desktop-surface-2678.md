# Desktop named in a card body no longer pins the card to its origin

Patches: **2568–2571**.

## Behavior

- Kanban placement used to keep a card on the machine that created it (a local pin, reason `local marker: mentions '<phrase>'`) whenever the card text named the Desktop app, for example "Desktop chat turns are held ..." in the body of a core fix that was landed by push. A body that only names the Desktop app as the symptom or the affected users no longer pins. Such a card is placed on the hub and is portable to the worker pool.
- Desktop-surface phrases (the product name plus the app, backend, renderer, window, chat, sidebar and UI forms) now pin only from the **title**, the same rule host names already follow. A title such as "Desktop app: composer loses focus" still pins.
- A body still pins when it tells the worker to operate the live app:
  - a chat or window the card itself creates, said with a qualifier (throwaway, scratch, disposable, hidden, fixture, fresh, new, real, live) before "Desktop chat" or "Desktop window", with or without "Hermes" in front;
  - an operate verb and a Desktop surface in the same sentence, in either order, at most 80 characters apart with no period, semicolon or newline between them ("Open Hermes Desktop and send one prompt", "In the Desktop app, open Settings", "Restart the Desktop backend");
  - the unchanged Desktop DOM, remote-debugging and app-bundle markers.
- The operate verbs (with their -s, -ing, -ed forms) are: open, launch, relaunch, restart, quit, focus, click, send, reload, drive, toggle, verify, reproduce, watch, confirm, use. Start, type, check, read, run and show are deliberately not verbs, so "turns are held at their start and end" does not match. "Desktop session" never pins on its own ("Each new Desktop session re-reads the config" is a loader fix).
- A caller that replaces the marker list owns it: every pattern in a replaced list scans the whole text, title and body.

## How it works

`hermes_cli/kanban_placement.py`.

- 2568 splits the Desktop markers out of the default list into named constants and adds them to the title-only set (`TITLE_ONLY_LOCAL_MARKERS`), so they are matched against the title only. A new live-chat marker keeps the "qualifier + Desktop chat/window" body pin.
- 2569 adds a verb-based action marker and lets the live-chat marker accept "Hermes Desktop", after review found three live instructions that had lost their pin.
- 2570 replaces that marker with one sentence-level rule matched in both word orders (verb before surface, surface before verb), widens the verb list and surfaces to the set above, and drops "desktop session(s)" from the live-chat marker. Every surface the action marker accepts is also a title-pinned phrase, so it pins nothing the previous behavior left unpinned.
- 2571 is test-only (below).

## Code

`hermes_cli/kanban_placement.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_placement_desktop_surface.py` (new in 2568, extended in 2569 and 2570) and `tests/hermes_cli/test_kanban_draining_machine_pin.py` (2571). Entry path: the placement decision helper and `portable_reason` in `kanban_placement`, plus the `kanban_create` tool handler, which the tests drive for body-symptom cards (placed on the hub, no local placement), operate-instruction cards (local pin) and a Desktop title (local pin). The tests cover symptom bodies, title pins, every operate verb in both orders, every surface beside a verb, bodies that must leave (verb in another sentence, verb too far away, non-verbs, "Desktop session") and a replaced marker list. Entry paths the change touches but no test in this group drives: the REST and dashboard card-create paths and the CLI `hermes kanban create`; they are **untested**.

Per entry:

- 2568: new test file, symptom bodies leave, title pins, live-chat bodies pin, replaced list scans the body, `kanban_create` end to end.
- 2569: adds symptom bodies and operate bodies, and `kanban_create` cases for the verb form.
- 2570: replaces the operate cases with the sentence-level cases above (verb inflections, both orders, all surfaces, not-operate bodies) and extends the `kanban_create` cases.
- 2571: test-only, no runtime behavior change. The draining-machine-pin test's card body only named the Desktop backend, which no longer pins; it now tells the worker to restart the backend, so the test still gets a local marker.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The pin decision is a regex over free text: a body that names the Desktop app and happens to contain an operate verb in the same sentence still pins, even when it is a symptom report ("send" and "use" are verbs).
- A card that needs the live app but is written as a symptom ("the Desktop chat showed no notice") now lands on the hub; add an operate sentence or a Desktop-surface title to keep it with the app.
- Hand-set local markers lists are unaffected by title-only matching: they scan the whole text.

## Plugin

Core change; no plugin required.
