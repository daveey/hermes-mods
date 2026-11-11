# Immediate contributed @ completions

Patch **2135**.

## Behavior

In-memory contributed mention rows can appear on the keystroke, without waiting for the completion debounce or the session owner's `complete.path` response. File-path results join after the asynchronous response arrives. This does not make remote path lookup synchronous.

## How it works

`useLiveCompletionAdapter` accepts an optional `instant(query)` callback. For a query whose fetched state is not ready, it schedules the normal fetch and computes immediate rows once per query within the adapter. `useAtCompletions` supplies contributed entries through the existing merge logic, including claimed handles. Source epochs invalidate the immediate callback when contributed rows change.

## Code

- `apps/desktop/src/app/chat/composer/hooks/use-live-completion-adapter.ts`: synchronous results alongside scheduled fetching.
- `apps/desktop/src/app/chat/composer/hooks/use-at-completions.ts`: contributed-source callback and adapter wiring.

## Tests

Source test inspected; **not executed in this mirror pass**:

- `apps/desktop/src/app/chat/composer/hooks/use-at-completions-contrib.test.tsx`: `shows contributed rows on the keystroke, before a slow complete.path answers`. It checks an immediate contributed result, a query change before the response, and eventual path-result inclusion with the contributed row first.

No live remote-gateway latency measurement was performed.

## Applying

Use the [core README full applying recipe](README.md#applying), including its pinned replay plan and integration bridges. This patch is not standalone.

## Gotchas

If immediate rows are empty, the adapter returns previously held fetched rows rather than guaranteeing an empty popover. Exceptions in the synchronous source become empty immediate results. Consumers without `instant` keep asynchronous behavior.

## Could this be a plugin?

Plugins can contribute in-memory completion sources, but showing their results before the built-in async fetch requires this core adapter change.
