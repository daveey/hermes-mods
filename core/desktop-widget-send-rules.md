# Desktop widget `hermes.send` rules in the agent hint, with refused intents shown under the frame

Patch: **1531** (`patches/1531-fix-desktop-tell-agents-the-widget-hermes.send-rules.patch`, fork `c3505ece7d9c`).

## Behavior

- **Agent hint.** `PLATFORM_HINTS["desktop"]` now gives agents the rules for inline-widget intents (`::preview` HTML
  widgets that call `window.hermes.send` / `data-hermes-send`):
  - At most 500 characters per send, and one send per second.
  - Each send returns a Promise of `{ok:true}` or `{ok:false,error,retryAfterMs}`. Await it, retry after
    `retryAfterMs` when throttled, and show the user any other failure.
  - Send large data in chunks.
  - The sandboxed frame has no `localStorage`, `sessionStorage` or `IndexedDB`. Keep state by writing it into the
    widget's file, and never promise the user local saving.

  Before this patch, these limits appeared only in a code comment. A widget that sent one over-length payload lost
  all of its data without telling the user.
- **Refusal notice.** When the frame refuses an intent as `too_long`, `invalid` or `undelivered`, the Desktop shows
  a one-line `role="status"` notice under the frame, for example "Widget message not sent: longer than 500
  characters. Ask the agent to send it in smaller pieces." The next accepted intent clears it. A `throttled` refusal
  adds no notice, because a correctly built widget retries it after `retryAfterMs`.

## How it works

- `agent/prompt_builder.py` appends the rules sentence to the desktop hint. The prompt-surface fixtures change to
  match: the `desktop.claude.txt` prompt text, and the `desktop.claude` stable prompt token count in
  `surfaces.json`, which goes from 1473 to 1587.
- `inline-preview-directive.tsx` exports `intentRejectNotice(ack)`. It returns `null` for `ok` and `throttled`, and
  returns a sentence for `too_long` (using `ack.maxLength`, falling back to `MAX_INTENT_LENGTH`), `invalid` (the
  prompt was empty after trimming) and `undelivered` (no composer accepted it).
- `InlineHtmlFrame` keeps `rejectNotice` state. After each intent decision it calls `setRejectNotice(notice)` when
  there is a notice or the intent was accepted. A throttled refusal therefore leaves any earlier notice unchanged.
  The ack posted back to the page is unchanged.

## Code

- `agent/prompt_builder.py`: `PLATFORM_HINTS["desktop"]`.
- `apps/desktop/src/components/assistant-ui/inline-preview-directive.tsx`: `intentRejectNotice` and the
  `InlineHtmlFrame` notice.
- Fixtures: `tests/fixtures/prompt_surface/prompts/desktop.claude.txt` and
  `tests/fixtures/prompt_surface/surfaces.json`.

## Tests

The patch adds or changes these tests. **They were read from the diff and not executed in this mirror pass.**

- `tests/agent/test_platform_hint_desktop.py::TestDesktopWidgetSendRules` (4 tests). The tests read
  `MAX_INTENT_LENGTH` and `INTENT_THROTTLE_MS` out of the renderer `.tsx` source with a regex, so the hint text
  cannot drift from the limits the frame enforces.
  - The hint contains "`<MAX_INTENT_LENGTH>` characters".
  - The throttle is 1000 ms and the hint says "one per second".
  - The hint mentions `{ok:false`, `retryAfterMs` and "chunk".
  - The hint mentions `localStorage` and "unavailable".
- `inline-preview-directive.test.ts`, `describe('intentRejectNotice')`:
  - A `too_long` notice contains "not sent" and the length cap.
  - `invalid` and `undelivered` notices contain "not sent".
  - `ok` and `throttled` return `null`.
- `inline-preview-directive.notice.test.tsx` (new): mounts the real `InlinePreviewDirective` with a mocked
  file read, composer submit and frame component, then dispatches intent `MessageEvent`s carrying the frame token.
  - An over-length intent renders a `status` notice containing "not sent", and the composer is not called.
  - An accepted intent submits once and renders no notice.
- The prompt-surface fixture tests pick up the updated prompt text and token count.

**Untested:**

- A notice being cleared by a later accepted intent.
- A throttled refusal leaving an earlier notice in place.
- Whether agents actually follow the hint. Only the hint text is tested, not model behavior.

## Applying

Apply in series order (`git am -3 --empty=keep`). The patch needs the earlier inline-preview intent channel
(`intentScript`, `evaluateIntent`, `INTENT_ACK_MESSAGE_TYPE`). The two fixture hunks conflict if another patch
changes the desktop hint, so rebase both fixtures together with the hint. No config is needed.

## Gotchas

- When `MAX_INTENT_LENGTH` or `INTENT_THROTTLE_MS` changes, update the hint text as well. The Python tests fail
  until both match. The throttle test is pinned to 1000 ms and the phrase "one per second".
- The notice reports only that an intent was refused. The page still decides whether to retry, and data it already
  dropped is not recovered.

## Plugin?

No. This is a core prompt and Desktop renderer change.
