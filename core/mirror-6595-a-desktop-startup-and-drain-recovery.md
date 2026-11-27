# Desktop startup-timeout recovery and gateway-drain retry

Patches: 5974, 5975, 5976, 5977, 6072, 6098, 6099, 6101, 6102, 6105.

## Behavior

- A window that hit the readiness deadline keeps its failure visible and re-probes with backoff (2 s doubling to 30 s; a bootstrap reset expedites it) until readiness returns; the deadline travels as typed data (`startup_deadline`) across Electron IPC rather than message text. Other start failures keep the fail-closed latch.
- Archive and prompt writes retry across a gateway restart's drain with stable identity (archive waits without a fixed budget; resume and identified prompt submits use a 600 s budget; plain session writes get a 150 s owner-restart window); owner discovery waits plain drains out within its starting budget, while an update pause stays final.
- When a send dies on a failed session resume, the notice distinguishes not sent, not queued and an unconfirmed earlier send; hidden sends retain the generic failure message. Only callers that restore or keep the text in the composer name that location; other callers name none. The rejected optimistic bubble is kept out of repaint. Drift aborts and most queue-owned sends suppress the ordinary notice.

## How it works

Electron main/preload, shared timeout classifiers and renderer hooks implement the transport; session_owner_discovery implements the backend wait.

## Code

Paths touched by these entries (non-test):

- `apps/desktop/electron/backend-ready.ts`
- `apps/desktop/electron/backend-start-failure.ts`
- `apps/desktop/electron/bootstrap-reset-windows.ts`
- `apps/desktop/electron/dashboard-token.ts`
- `apps/desktop/electron/local-gateway.ts`
- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/preload.ts`
- `apps/desktop/electron/session-owner-list-route.ts`
- `apps/desktop/src/api/client.ts`
- `apps/desktop/src/api/gateway-drain.ts`
- `apps/desktop/src/api/session-write-reconnect.ts`
- `apps/desktop/src/app/chat/composer/gateway-restart-notice.tsx`
- `apps/desktop/src/app/chat/composer/hooks/use-composer-queue.ts`
- `apps/desktop/src/app/chat/composer/hooks/use-composer-submit.ts`
- `apps/desktop/src/app/chat/composer/index.tsx`
- `apps/desktop/src/app/gateway/hooks/boot-timeout-recovery.ts`
- `apps/desktop/src/app/gateway/hooks/use-gateway-boot.ts`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/rejected-optimistic-sends.ts`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/resume-across-restart.ts`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/single-flight-resume.ts`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit-across-restart.ts`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/utils.ts`
- `apps/desktop/src/app/session/hooks/use-session-actions/archive-commit.ts`
- `apps/desktop/src/app/session/hooks/use-session-actions/archive-drain-owner.ts`
- `apps/desktop/src/app/session/hooks/use-session-actions/archive-drain-write.ts`
- `apps/desktop/src/app/session/hooks/use-session-actions/archive-owner-list.ts`
- `apps/desktop/src/app/session/hooks/use-session-actions/fresh-draft-options.ts`
- ... and 24 more paths in the same commits

## Tests

Entry-path behavior above is source-reviewed but **untested in this review**. The files below identify source regression targets, not proof that this review exercised those entrypoints.

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `apps/desktop/electron/backend-start-failure.test.ts`
- `apps/desktop/electron/bootstrap-reset-windows.test.ts`
- `apps/desktop/electron/review-readiness-deadline.test.ts`
- `apps/desktop/electron/session-owner-list-route.test.ts`
- `apps/desktop/src/api/backend-write-ipc.test.ts`
- `apps/desktop/src/api/session-write-reconnect.test.ts`
- `apps/desktop/src/api/session-write-retry.test.ts`
- `apps/desktop/src/app/chat/composer/gateway-restart-notice.test.tsx`
- `apps/desktop/src/app/chat/composer/hooks/use-composer-queue.test.tsx`
- `apps/desktop/src/app/chat/composer/hooks/use-composer-submit.restore-scoping.test.tsx`
- `apps/desktop/src/app/chat/composer/hooks/use-composer-submit.test.tsx`
- `apps/desktop/src/app/gateway/hooks/boot-timeout-recovery.test.ts`
- `apps/desktop/src/app/gateway/hooks/use-gateway-boot-timeout.test.tsx`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/drain-resume.test.ts`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/rejected-optimistic-sends.test.ts`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit-drain.test.tsx`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit-resume-failed.test.tsx`
- `apps/desktop/src/app/session/hooks/use-session-actions/archive-commit.test.ts`
- `apps/desktop/src/app/session/hooks/use-session-actions/archive-drain-owner.test.ts`
- `apps/desktop/src/app/session/hooks/use-session-actions/resume-latches.test.ts`
- `apps/desktop/src/test/startup-connection-bridge.test.ts`
- `tests/hermes_cli/test_session_owner_discovery_draining.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Late-window recovery only; not a slow-start diagnosis.
- The resume-failure latch is bounded.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this is the desktop app and backend session owner discovery themselves.
