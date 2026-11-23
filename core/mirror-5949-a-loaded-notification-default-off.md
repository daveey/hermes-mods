# Loaded-session notification source defaults off

Patch coverage: **5924**.

## Behavior

`display.loaded_notification_f` now defaults to `false`, and a missing `display` section or missing key also resolves to `false`. Newly initialized loaded sessions then use the legacy per-chat notification path. Explicit `true` still admits the session as a lane of the shared loaded-session executor. Notification delivery is unchanged either way; only the scheduling path differs. Sessions already admitted keep their lane (no migration).

## How it works

`notification_loaded_f.enabled()` reads `load_config()['display']` and compares the value with `True`, with the fallback now `False`. `session_notifications._start_notification_poller` picks the shared lane when enabled and the legacy thread otherwise. The `DEFAULT_CONFIG` entry and the user-guide text change to match. This is a rollout containment switch, not a fix of the shared source's cost; the docs tell operators to keep it off until that cost is corrected and verified.

## Code

- `hermes_cli/config_defaults.py`: default `False`.
- `tui_gateway/notification_loaded_f.py`: `enabled()` fallback `False`.
- `tui_gateway/session_notifications.py`: docstring now says opt-in.
- `website/docs/user-guide/configuration.md`: Display Settings text.

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live deployment or performance result is claimed.

`tests/tui_gateway/test_loaded_notification_f.py`: explicit `true` still registers on the shared source; the legacy-polling parity test is parametrized over explicit-off, missing `display`, missing key and the real `DEFAULT_CONFIG`. Untested entry path: a full running gateway starting sessions under each setting; only config selection and registration are exercised.

## Applying

Apply the covered patches only as part of the full ordered core series, following the whole segmented recipe in the [core README Applying section](README.md#applying) (replay bridges and required repair overlays included). The mirror is append-only and these entries depend on earlier ones; do not run a standalone `git am` on them and do not treat them as an independent cherry-pick set. A reimplementation on a newer upstream should keep the invariants above and run the named suites in a disposable source checkout.

## Gotchas

- Changing the value affects only sessions initialized afterwards.
- An explicit `true` in a user's config keeps the shared path, so rollback for such users means editing their config.
- The default change alone is not evidence that the shared source is cheaper or correct.

## Why not a plugin

No: it is a core config default read at session start inside the TUI gateway's notification startup, which has no plugin hook.
