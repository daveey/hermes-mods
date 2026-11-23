# Queued archive preflight with renderer custody

Source patch ordinals: 5801–5804.

## Behavior

Transport-unreachable archive preflight stays queued until reconnect or explicit cancellation, rather than timing out into an unsafe override. Recovery reruns safety checks before mutation. A renderer restart reports lost intent rather than silently replaying it; another live window must not retire the intent.

## How it works

Archive queue lifetime tracks cancellation and durable existence markers, not a safety verdict or replayable mutation. Storage enumeration uses `Storage.length`/`key`. An origin-wide Web Lock holds custody for each attempt; orphan detection removes a marker only when it can obtain that lock nonblocking. Pending lock acquisition respects disposal. Read deadlines bound each read, not the whole user intent. The fixture follow-up isolates archive Web Locks setup from unrelated composer paths.

## Code

- `apps/desktop/src/app/session/hooks/use-session-actions/archive-queue-lifetime.ts`
- `apps/desktop/src/app/session/hooks/use-session-actions/archive-queue-lock.ts`
- `apps/desktop/src/app/session/hooks/use-session-actions/reconnect-archive-preflight.ts`
- `apps/desktop/src/app/session/hooks/use-session-actions/refresh-archive-tasks.ts`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

Archive deadline, durable-queue, queue-lifetime, peer, reconnect and linked-confirmation suites in `apps/desktop/src/app/session/hooks/use-session-actions/` cover long outages, fresh safety on recovery, cancellation without PATCH, hung reads, restart markers, peer-window custody and lack of Web Locks support. `apps/desktop/src/test/web-locks.test.ts` covers blocking/nonblocking fixture behavior and the composer's two-argument API.

## Applying

Apply the lifetime, reconnect, storage and peer-lock follow-ups together. Preserve the existing non-transport unknown confirmation path; rebuild the Desktop client normally.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

A marker is not a durable operation queue. Reloaded intents are dropped with a standing notice, not resumed. Queueing without origin-wide custody support fails visibly. Safety checks are repeated after recovery; an old preflight result never grants future archive permission.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
