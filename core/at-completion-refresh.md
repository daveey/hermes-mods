# `@` completion sources can redraw an open popover

Patch **0458** (source `fb72ad495b`).

## Behavior

A `composer.atCompletions` source may also offer `subscribe(onChange) → unsubscribe`. When it calls `onChange`,
an `@` popover that is already open asks every source again for the query it is showing, so rows a source
fetched in the background appear without another keystroke. Sources without `subscribe` work as before.

Before this, the popover only refetched when the typed query changed. A source that answers from its own cache
(as the contract asks) could not get rows that arrived after the keystroke into the open popover. That is how a
cold `@the` missed a session that existed and scored highest (see `desktop-plugins/session-mentions`).

## How it works

- `apps/desktop/src/app/chat/composer/contrib.ts`: `ComposerAtCompletionSource.subscribe?` added to the contract.
- `apps/desktop/src/app/chat/composer/hooks/use-at-completions.ts`: an effect subscribes to every contributed
  source and bumps a `sourceEpoch` counter on each signal. The hook passes it as `epoch` to
  `useLiveCompletionAdapter`, whose existing epoch path drops the held query so the next `search()` refetches,
  keeping the old rows on screen until the new answer lands. A throwing `subscribe` is skipped, like a throwing
  `provide`. Re-subscribes when the contribution list changes and unsubscribes on unmount.

## Code

`apps/desktop/src/app/chat/composer/contrib.ts`, `apps/desktop/src/app/chat/composer/hooks/use-at-completions.ts`.

## Tests

`apps/desktop/src/app/chat/composer/hooks/use-at-completions-contrib.test.tsx`:
- `re-asks an open popover when a source signals its rows changed`: the same query returns the new row after the
  signal, and unmount removes the listener. Fails without the epoch wiring.

The plugin side (`plugins/session-mentions/tests/typeahead-refresh.test.mjs`) tests that session-mentions signals
when a fetch lands or fails. The two halves have not been exercised together in a running Desktop by a test.

## Applying

Independent of the other patches; touches only the two files above. If `useLiveCompletionAdapter` loses its
`epoch` option upstream, re-add an equivalent "forget the held query" trigger and call it from the subscription.

## Gotchas

- Every signal refetches the popover, including the gateway's `complete.path` RPC for uncached queries. Sources
  should coalesce signals (session-mentions batches them over 30 ms) and signal only on real changes.
- A source must not signal from inside `provide()` synchronously on every call, or the popover refetches in a loop.

## Plugin?

No. Only core owns the popover's refetch decision; a plugin can't reach the adapter's epoch.
