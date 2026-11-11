# Deterministic external-draft replacement regression test

Patch **2156**; test-only change.

## Behavior

This strengthens regression coverage for an external draft replacement arriving while a deferred key-burst flush is pending. It changes no production TUI behavior. The intended invariant remains: once history navigation replaces the draft, the pending stale burst must not overwrite that replacement or be emitted to the parent.

## How it works

The test enables React's act environment and wraps mounting, input delivery, external replacement, timer advancement and cleanup in awaited `act` calls. This provides a commit/effect barrier before advancing the burst timer; a pair of `setImmediate` ticks alone does not guarantee that barrier on a busy runner.

A `performance.now` spy forces scheduler time forward during replacement. Intermediate assertions require parent emissions to remain `['a', 'abc']` after the next character arms a deferred burst. After replacement and timer advancement, that emission list must remain, and the unmount snapshot must hold the history draft and its end cursor.

## Code

- `ui-tui/src/__tests__/textInputStaleParentEcho.test.tsx`: React `act` import and external-replacement case.
- No production `TextInput` implementation changes in this patch.

## Tests

Source test inspected; **not executed in this mirror pass**:

- `ui-tui/src/__tests__/textInputStaleParentEcho.test.tsx`, suite `stale parent own-echo during deferred key-burst flush`: `drops a pending key-burst flush when an external value replaces the draft`.

This is a fake-TTY test of external assignment, not a live history-navigation or slash-completion integration test. Those entry paths were not exercised here.

## Applying

Use the [core README full applying recipe](README.md#applying), including its pinned replay plan and integration bridges. This patch is not standalone.

## Gotchas

Fake timers cover timeout/interval/Date while React scheduling remains real. Preserve the awaited replacement barrier before advancing the burst clock; arbitrary extra ticks weaken the intended ordering. Restore the act-environment stub and mocks during cleanup.

## Could this be a plugin?

No plugin is needed: this is test-harness synchronization for the core text input, not a runtime extension.
