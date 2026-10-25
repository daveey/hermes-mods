# Kanban failure notices: cause, retry state, owner

Patches:

- `patches/0216-fix-kanban-gave_up-crashed-notices-name-the-real-cau.patch`: shared classifier
  `hermes_cli/kanban_failure_summary.py`, used by the Desktop/TUI notice (`tui_gateway/session_notifications.py`).
- `patches/0217-fix-kanban-gateway-failure-notices-share-the-classif.patch`: the messaging gateway notifier
  (`gateway/kanban_watchers_notifier.py`) uses the same classifier; retry/refusal wording corrected.

## Behavior

- `gave_up` / `crashed` notices say what happened: model refusal, provider configuration problem, protocol
  violation (no result reported), launch failure, time limit, or an unexpected process exit. A connection refusal is
  not a model refusal. Raw pid / exit code / worker output appears only under `Details:` (ANSI stripped).
- Retry is promised only when the event records `retry_status` of `ready` or `review` ("the dispatcher's normal retry
  policy applies"). `blocked` says the task is Blocked. A missing or `todo` status says "retry state was not recorded".
  `timed_out` notices follow the same rule (Desktop and messaging gateway).
- A model refusal is reported as a refusal that needs review. The text makes no claim about alternate providers and
  does not suggest a way around the refusal; nothing in this change alters retry, fallback or provider policy.
- A terminal-provider exit (code 78) says "provider configuration problem" and points at the worker log: that exit
  covers several provider reasons and the event does not record which one fired, so no credential is named.
- No user action is asserted unless the class implies one (provider configuration).

## How it works

`classify_failure(kind, payload)` reads `error` / `worker_output` / `reason`, `terminal_provider`,
`trigger_outcome`, `protocol_violation(s)`. `describe_failure` builds the body; `retry_promised(payload)` is the single
retry gate. The gateway formatter prepends the task head and appends a `hermes kanban log <id>` pointer. The old
localized `gateway.kanban.ping.gave_up` / `crashed` strings are no longer used by these two kinds (the English-only
wording matches the Desktop notice); the no-retry-claim `timed_out` text is English-only inline for the same reason
(a new locale key must exist in every catalog or `tests/agent/test_i18n.py` fails).

## Code

- `hermes_cli/kanban_failure_summary.py`, `tui_gateway/session_notifications.py` (`_kb_failure`, `_kb_timed_out`).
- `gateway/kanban_watchers_notifier.py` (`_fmt_failure`, `_fmt_timed_out`).

## Tests

- `tests/tui_gateway/test_kanban_failure_notice_text.py`: refusal vs spawn failure vs connection refused, ANSI,
  retry gate (missing / `todo` / `blocked` / `ready`), terminal provider, `timed_out` (Desktop path).
- `tests/gateway/test_kanban_failure_notice_gateway.py`: the same classes through the messaging gateway formatters
  (`gave_up`, `crashed`, `timed_out`).
- `tests/gateway/test_kanban_notifier.py` end-to-end delivery updated to the new crash wording.
Passed on `origin/main` 8d58a86237 together with `tests/agent/test_i18n.py`. Untested: non-English locales (the new
wording is English-only), and real-delivery to Telegram/Slack adapters beyond the recording adapter.

## Applying

`git am -3` in order, 0216 then 0217.

## Gotchas

- Not a retry-policy change: whether the dispatcher retries, falls back, or blocks is decided elsewhere; this only
  words what the event records.

## Plugin?

No: the notice text lives in the core notifier and TUI gateway; no plugin hook sits on those formatters.
