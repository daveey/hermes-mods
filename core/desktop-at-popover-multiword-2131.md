# Desktop: multi-word @ completion sources

Patch **2101**.

## Behavior

A `composer.atCompletions` contribution may set `multiWord: true`. While the open `@` popover holds a row from such a source, typing a space widens the same token (max 6 words) and the source receives the spaced query (e.g. `demo bear`). The popover closes immediately when the settled answer has no multi-word row, so prose after `@name ` behaves as before. Never across a newline, another `@` or a chip, and never for `@file:`-style scoped tokens. Sources without the flag never see a space. Documented in the Desktop plugin SDK page.

## How it works

`contrib.ts` adds the flag; `use-at-completions` tags rows with `metadata.multiWord='1'`; `continueAtTrigger` in `text-utils.ts` returns a continuation only when the previous token matches, the query contains whitespace, and the word cap is respected; `use-composer-trigger` calls it after `detectTrigger` when the items include a multi-word row; the live adapter passes the flag through.

## Code

- `apps/desktop/src/app/chat/composer/contrib.ts`, `hooks/use-at-completions.ts`, `hooks/use-composer-trigger.ts`, `hooks/use-live-completion-adapter.ts`, `text-utils.ts`.
- `website/docs/developer-guide/desktop-plugin-sdk.md`.

## Tests

**Source regression coverage; not rerun in this documentation pass.** `use-composer-trigger-multiword.test.ts`: popover stays open across a space; closes when the widened query stops matching; picking replaces the whole multi-word token; controls for non-multiWord rows and Escape; `continueAtTrigger` guards (scope, newline, different token, six-word cap, only `@`). `use-at-completions-contrib.test.tsx`: rows from a multiWord source are tagged. Real session-title sources are not exercised.

## Applying

Apply ordinal(s) 2101 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. Related: [at-completion-refresh.md](at-completion-refresh.md). No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

The popover only widens while a multi-word row is shown; a slow source can close it before the second word arrives. Sources must tolerate spaces in `provide`.

## Could this be a plugin?

The flag is consumed by plugins, but the trigger parsing is core composer code, so the feature itself is not plugin-only.
