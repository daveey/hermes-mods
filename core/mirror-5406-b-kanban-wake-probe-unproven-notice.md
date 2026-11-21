# Kanban: one early notice for a wake probe that cannot pass under a far backstop

Mirror entries 5188-5191.

## Behavior

- After at least 3 clean exit-1 checks within the current park spanning more than `kanban.wake_check_notice_hours` (default 6) while the park's `wake_at` backstop is more than 24 hours away, one passive `wake_probe_unproven_notice` is appended naming the probe and its last output. It is delivered to the origin chat by the gateway and TUI notifiers and never wakes the card. One per park episode (a re-park opens a new one); clock-only parks never notify; probe semantics, backstops, admission and the 48 h wake are unchanged.
- 5189: the config key is registered and documented; the streak counts consecutive checks per park (a spent park is ignored, a failed probe breaks the streak, age starts at the first check), honors passive intake, and is isolated from the portable stale-wake episode.
- 5190: a crashed probe posing as exit-1 (`last_rc=1` with `fails` set) breaks the streak, matching the passive-intake quiet rule.
- 5191: the notice age is snapshotted before probes are submitted, so async submission uses the dispatcher's scope and a probe row that preserves the park resets on upsert.

## How it works

- `hermes_cli/kanban_wake_unproven.py` (streak and notice), `kanban_wake_check.py` (snapshot and hook), `config_defaults.py`, `website/docs/user-guide/configuration.md`.
- `gateway/kanban_watchers_notifier.py` and `tui_gateway/session_notifications.py` deliver the new passive kind.

## Code

- `hermes_cli/kanban_wake_unproven.py`
- `hermes_cli/kanban_wake_check.py`
- `hermes_cli/config_defaults.py`
- `gateway/kanban_watchers_notifier.py`
- `tui_gateway/session_notifications.py`

## Tests

Source tests (not executed in this mirror pass): `tests/hermes_cli/test_kanban_wake_unproven.py` (notifies exactly once, under-age and near-backstop silence, re-park new episode, clock-only silence, delivered passive kind, minimum three checks, exact age/backstop boundaries, configurable age, registered key, passive listener, failed probe and exit-1-with-error break the streak, spent park, async notice age). Actual notifier delivery to a chat is covered only by the passive-kind test.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Detection only; it does not stop or change the probe.
- Setting the age key too low produces notices for slow-but-healthy probes.

## Why core instead of a plugin

The notice rides the core wake-check episode and the built-in gateway/TUI notifier kinds, plus a registered core config default.
