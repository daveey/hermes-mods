# Configurable errors/gateway/gui log rotation; INFO trace for dropped process completions

Patches: **2700, 2701**.

## Behavior

- `errors.log` (default 2 MB x 2 backups), `gateway.log` (5 MB x 3) and `gui.log` (10 MB x 5) rotation was hard-coded, so a busy hub kept about an hour of history. `logging.<stem>_max_size_mb` and `logging.<stem>_backup_count` in `config.yaml` (stem `errors`, `gateway` or `gui`) now size them. Unset, zero, negative or non-numeric values keep the old defaults, per key. `agent.log` already followed `logging.max_size_mb` / `logging.backup_count`.
- A process completion that is dropped now leaves exactly one INFO line: `Dropped completion notification for process <proc id> (session=<key>): <why>`. The three drop sites are: the notification park (retention window expired, or displaced by a newer result), the unowned-event path (park refused, or the shutdown drain), and the process registry's parked-event box overflowing (the evicted event is logged, with the box size). Previously these were DEBUG or silent, so a chat that never woke for a re-adopted exit left no trace.
- Progress notices (`watch_*`, `heartbeat`) stay at DEBUG. Delegation notifications keep their existing WARNING (or DEBUG for a restored replay) wording.

## How it works

2700: `hermes_logging.py` reads the whole `logging` mapping once (`_read_logging_section`; `_read_logging_config` still returns the level/size/backup triple for existing callers). `_file_rotation(log_cfg, stem, size, count)` applies `_positive_int` to the two per-stem keys and falls back to the default size and count. `setup_logging` feeds the result into the `errors.log`, `gateway.log` and `gui.log` handler specs. `tui_gateway/notification_park.py` gains `log_drop(evt, why)` (DEBUG for progress notices, INFO otherwise) and uses it when a non-delegation event expires from the park; `tools/process_registry_readopt.py` logs the events it evicts when the parked-event box exceeds `_MAX_PARKED_EVENTS`; `tui_gateway/session_notifications.py` calls `log_drop` when an unowned event cannot be parked.

2701: a refactor of the same drop path in `_notif_unowned_event`, with no behavior change. The delegation branch now holds the `restored` check and the `return_completion_offer` call, and the non-delegation branch holds the `log_drop` call, which keeps `session_notifications.py` at its 2000-line cap.

## Code

`hermes_logging.py`, `tui_gateway/notification_park.py`, `tui_gateway/session_notifications.py`, `tools/process_registry_readopt.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/test_hermes_logging.py` (two new rotation tests) and `tests/tui_gateway/test_notification_park_drop_log.py` (four tests). Entry path: `setup_logging` with a `config.yaml` in a temp home and the rotating-handler factory replaced by a recorder (agent, errors, gateway and gui sizes, an invalid value, unchanged defaults), and the park, `_notif_unowned_event` and `ProcessRegistry.park_unowned_event` APIs with log capture (retention expiry, progress notice stays DEBUG, park refusal and shutdown drain, box overflow). Actual file rotation with the new sizes, the delegation branch after the 2701 refactor, and displacement of a parked event by a newer result are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Settings take effect at logging setup (process start); an already-running gateway keeps its old sizes until restarted.
- Values are whole megabytes and whole backup counts; `0` is treated as unset, not as "no rotation".
- The INFO line is per dropped event; a flood of drops writes a flood of lines.

## Plugin

Core change; no plugin required.
