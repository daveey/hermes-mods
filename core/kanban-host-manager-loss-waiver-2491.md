# Host manager-loss waiver for owner-death repeats

Patches: **2454, 2461**.

## Behavior

The crash-loop guard charged a card as `owner_death_repeat` when it was in flight at two unclean deaths of its owning gateway. When a worker host's systemd user manager is SIGKILLed, every card in flight at that moment dies with the gateway, so bystanders were charged although the host, not the card, failed. Now, if a root-owned recovery receipt on the worker host attests that this exact owner died with a SIGKILLed user manager, one repeat per card is waived: the run is requeued as an ordinary owner-restart loss with no failure counted, and its error text names the root incident. A card that triggers a second waiver-eligible repeat is charged on that repeat. Missing, legacy, unreadable, untrusted or non-matching receipts change nothing.

Patch 2461 makes the reader fail closed on hostile numbers: a receipt carrying a JSON integer too large for a float (or inf/nan) is simply no evidence, and any exception from the reader leaves the repeat charged (logged with a traceback) instead of escaping into the dead-worker sweep.

## How it works

`kanban_host_manager_loss.manager_loss_incident(receipt)` takes the run's `owner_admitted` payload (`pid`, boot-relative `birth`) and returns the root incident id or `None`. It is Linux-only and requires the recovery directory (`/var/lib/hermes-worker-recovery`) and every ancestor to be root-owned, non-symlink and not group/other-writable. It scans `status.json` plus the 16 newest `incidents/*.json` files (each root-owned, regular, non-symlink, not group/other-writable, at most 8 KiB, opened with `O_NOFOLLOW`). A record matches only if its `manager_loss` block has schema 1, this boot id and host, unit `user@1000.service`, `exit_code` `killed` with `exit_status` 9, an owner pid and birth equal to the admitted owner (birth tolerance 5 ms), and monotonic ordering `owner birth <= observed <= exit <= now` with at most 60 s between snapshot and exit. `_number` accepts only finite numbers.

`kanban_owner_death._host_manager_loss_waiver` counts earlier ended runs of the task whose metadata has `host_manager_loss`; if it reaches `MAX_WAIVERS` (1) it returns `None`, otherwise it consults the reader, wrapped in `except Exception`. In `note()`, a waived repeat writes `host_manager_loss` (incident id) and `owner_death_repeat_waived` (previous owner pid) into the payload and is rebooked as `owner_restart_lost`. The receipt itself is produced by a separate root watchdog on the worker host that is not part of this repository. See [kanban-owner-restart-lost.md](kanban-owner-restart-lost.md) for the owner-restart rebooking this extends.

## Code

`hermes_cli/kanban_host_manager_loss.py`, `hermes_cli/kanban_owner_death.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_host_manager_loss.py`. Covered entry paths: the reader function directly (exact match, many mutated receipts, trust checks on files, symlinks, size, ancestors, huge numbers, non-Linux) and the dead-worker sweep `kbd.detect_crashed_workers` end to end (waived once then charged, no evidence charged, overflowing receipt charged, first death keeps the waiver) plus `_host_manager_loss_waiver` on reader errors. No CLI or REST path is involved. The real root watchdog and real root-owned files are untested (tests use the current user's uid as the trusted uid).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Evidence is cohort evidence, not proof of who sent the signal; a card can still kill its own manager, hence at most one waiver per card.
- The trusted directory, manager unit name (`user@1000.service`) and uid 0 are hard-coded; other layouts get no waiver.
- Without the watchdog's receipts the feature is inert.

## Plugin

Core change; no plugin required.
