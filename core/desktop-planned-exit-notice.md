# A core-update restart is not a crash; backend-exit cards dedupe and clear

Patch: `patches/0972-fix-desktop-a-core-update-restart-is-not-a-crash-exi.patch` (source `77e6a1eda3`).

## Why

`hermes-sync`'s core step runs `hermes update --yes`, which stops the Desktop-owned local backend (SIGTERM) while
it holds the update marker. On 2026-10-29 that happened at 07:29, 08:14, 08:38 and 09:10, and each time Desktop
showed the red "Hermes stopped working in the background — closed unexpectedly. Restart it" card. Nothing had
crashed: the supervisor respawned the backend, parked on the marker until the update exited, and the new backend
was ready 8 s later. The cards stayed up anyway and piled up ("13 more notifications"). The same SIGTERM during a
core update produced a stuck "Hermes couldn't start" overlay on 2026-10-28 22:18.

```
Problem: 4 red "stopped working in the background" cards on 2026-10-29 (desktop.log "Hermes backend exited
  (SIGTERM)" at 07:29:09, 08:14:56, 08:38:52, 09:10:01), each 1 s before hermes-sync's "core: update exit 0".
Why 1: the renderer got `hermes:backend-exit` and raised the crash card - use-gateway-boot.ts onBackendExit
  treats every exit as a crash (notify kind 'error').
Why 2: main sent the event without knowing the exit was planned - scheduleUnexpectedPrimaryRecovery() calls
  sendBackendExit() first and only then respawns; the respawn's waitForUpdateToFinish() is what reads the marker
  ("[updates] update in progress (marker); deferring backend start", 8 ms after the exit line).
Why 3: the "is this a planned restart?" fact (live update marker, update in flight, update hand-off) had one
  consumer, the respawn gate; the exit reporter decided from the exit alone. Soft re-home was the only planned
  teardown it knew (softRehomeInProgress), because that one is started inside Desktop.
Why 4 (pile-up): the card had no stable id and durationMs 0, and no code dismissed it when the backend came
  back, so every outage added a card that only a click removed.
Root (happened): exit reporting and the update gate read different sources of truth; the card's lifetime was not
  tied to the outage it reports.
Root (not caught): the hook tests pinned only "an exit after boot toasts a Restart" and "an exit during boot fails
  the overlay"; none drove an exit while an update marker was live, a repeat exit, or a reconnect after one.
```

## What it does

- **Main classifies the exit** (`electron/backend-exit-report.ts`, `createBackendExitSender`): an exit while
  `updateInFlight`, the update hand-off, or a live update marker (`readLiveUpdateMarker`) is sent with
  `planned: 'update'` and logs `[supervisor] backend exit during a planned update; renderer shows the quiet updating
  notice`. If the probe throws, the exit is reported as unexpected, so a real crash is never hidden. Soft re-home
  stays silent as before.
- **Renderer** (`use-gateway-boot.ts`): a planned exit after boot shows one info notice "Updating Hermes…"
  (id `backend-updating`, no Restart button); during boot it does nothing (main's boot progress already says an
  update is finishing) instead of failing the overlay. An unexpected exit still shows the red card, now with the
  stable id `backend-exit`, so repeats replace it. The gateway socket's reopen dismisses both.
- ja/ru/zh boot copy moved to `ja_boot.ts`/`ru_boot.ts`/`zh_boot.ts` like the other locales (code-health caps).

## Tests

- `electron/backend-exit-report.test.ts`: planned (live marker) SIGTERM is marked planned; no update is unexpected;
  a throwing probe is unexpected; the sender sends planned/unexpected payloads and stays silent during soft re-home.
- `src/app/gateway/hooks/use-gateway-boot-exit-notice.test.tsx` (real hook, fake socket): planned exit after boot
  shows only the updating notice; planned exit during boot does not fail the boot; three unexpected exits keep one
  crash card; both cards clear once the socket reopens. All four fail on the previous hook.
- Untested: the wiring of `isPlannedRestart` to main's state in `main.ts` (verified live: the log line above
  after the next core update).
