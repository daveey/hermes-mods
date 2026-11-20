# Session delivery and safe reconnect behavior

Mirror entries: 4453, 4454, 4460, 4470, 4498, 4499.

## Behavior

- **4453** — Watcher replay coverage requires the expected sessions.changed output on every peer, not merely lower CPU.
- **4454** — Invalid or incomplete notice span reconstruction preserves the entire opaque carrier and omits dedupe identity.
- **4460** — Review wakes say only that work was handed off for review and refresh stale status at fold/drain time.
- **4470** — Unseen streamed interim boundaries remain available to downstream segment consumers.
- **4498** — Re-adopted job-exit waking moves to a sibling registration module without changing delivery behavior.
- **4499** — Desktop session write retries spend one reconnect budget and do not replay identity-less legacy writes.

## How it works

Watcher tests now record every peer's event histogram and frame count, so a stopped watcher or one-peer fanout cannot appear to be an optimization. Notice constituent tests require complete display/model reconstruction and valid nonboolean, nonnegative, in-range spans before extracting dedupe identity; otherwise opaque bytes remain intact.

Review wake text no longer asserts implementation completion. Folding checks current local review/run state, keeps pending reviewer wakes, and avoids inferring a run lane from an ambiguous newer remote running row. Interim publication records whether an execution event was actually published; a segment neither already streamed nor published stays unseen for consumer delivery.

Re-adopted exits still park for the chat poller, wake unloaded chats in-process, and leave refused/missing chats parked. The extracted module binds helpers to server globals so existing lookups and monkeypatches remain valid.

Desktop fenced writes receive the remaining reconnect window on every attempt. The snapshot hop consumes that window; the subsequent PATCH/DELETE recomputes time remaining, caps each hop at 30 seconds and never sends a zero/unlimited timeout after expiry. Legacy writes lacking receipt identity wrap failures as nonreplayable while preserving message and cause.

## Code

- `apps/desktop/src/api/session-write-reconnect.ts`
- `apps/desktop/src/api/sessions.ts`
- `gateway/kanban_wake_fold.py`
- `gateway/run_turn_runner.py`
- `locales/af.yaml`
- `locales/ar.yaml`
- `locales/de.yaml`
- `locales/en.yaml`
- `locales/es.yaml`
- `locales/fr.yaml`
- `locales/ga.yaml`
- `locales/hu.yaml`
- `locales/it.yaml`
- `locales/ja.yaml`
- `locales/ko.yaml`
- `locales/pt.yaml`
- `locales/ru.yaml`
- `locales/tr.yaml`
- `locales/uk.yaml`
- `locales/zh-hant.yaml`
- `locales/zh.yaml`
- `tui_gateway/session_notifications.py`
- `tui_gateway/session_notifications_readopt.py`

## Tests

Source tests assert all-peer watcher output, opaque-span fallback, localized review wording and review-state transitions, streamed consumer segments, and fake-clock Desktop reconnect/write cases including snapshot expiry and lost legacy DELETE replies. The re-adoption entry is a structural extraction and adds no new test file; this pass has not freshly exercised its runtime path.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `apps/desktop/src/api/session-write-reconnect.test.ts`
- `apps/desktop/src/api/sessions.test.ts`
- `tests/agent/test_notice_constituent_fallback.py`
- `tests/gateway/test_interim_segment_delivery.py`
- `tests/gateway/test_kanban_notifier.py`
- `tests/gateway/test_kanban_review_wake_locales.py`
- `tests/gateway/test_kanban_wake_fold.py`
- `tests/tui_gateway/test_j_nonrpc_rows.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

Safe retry relies on fenced mutation identity; transport cancellation still owns actual request aborts, and JavaScript scheduling is not hard real time. Unknown remote run lanes are intentionally not relabeled. A review handoff is not implementation completion, and opaque notice fallback is deliberately not deduplicated using unproved spans.
