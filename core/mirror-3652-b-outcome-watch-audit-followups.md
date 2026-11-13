# Outcome watch mark: audit and Desktop label follow-ups

Patches: **3641**.

## Behavior
- The outcome audit for a done chat that both owns a cron and watches a card now reports every applicable reason joined (`done_owns_cron+done_watching`), with both item lists in `on`, matching the tooltip. Single-reason rows are unchanged.
- The Desktop wake label for a wake more than 6 days out includes the date (`Wed Nov 25 09:00`) instead of the ambiguous `Wed 09:00`.
- The Desktop tooltip leads with the watched card's title, e.g. `verify the re-run limit (t_watch1, wakes Wed 09:00)`; an untitled card keeps the `t_… (wakes …)` form.
- A locked or busy board is no longer silently read as "no subscriber watches": other sqlite errors reach the caller's guard, which logs them. A board lacking the notify-subscriptions table still reads created watches.

## How it works
- 3641 turns the watch branch of the audit from `elif` into an additive reason in `hermes_state_outcome.py` / `sessions_cmd_outcome.py`.
- `_derive_current` now reads crons and watches only after the declared-waiting and card-read-failed returns.
- `kanban_watch._read_chat_watches` checks that `kanban_notify_subs` exists and no longer swallows every `sqlite3.Error`.
- Desktop `session-outcome.ts` and the English/type i18n files carry the date and title wording.

## Code
- `hermes_cli/kanban_watch.py`
- `hermes_cli/sessions_cmd_outcome.py`
- `hermes_state_outcome.py`
- `apps/desktop/src/lib/session-outcome.ts`
- `apps/desktop/src/i18n/en_session_row_status.ts`
- `apps/desktop/src/i18n/types_session_row_status.ts`
- `apps/desktop/src/lib/session-outcome.test.ts`
- `apps/desktop/src/app/chat/sidebar/session-row-stripe.test.tsx`
- `tests/tools/test_kanban_watch_park.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_kanban_watch_park.py`: `test_audit_names_cron_and_watch_together`, `test_declared_waiting_does_not_scan_boards_for_watches`, `test_locked_board_is_not_silently_read_as_no_subscriber_watches`, `test_board_without_notify_subs_reads_created_watches`.
- `apps/desktop/src/lib/session-outcome.test.ts`: `stays green and names the pending check (title and id) with its wake time`, `dates a wake more than 6 days out`.
- `apps/desktop/src/app/chat/sidebar/session-row-stripe.test.tsx` was modified; no new test names added.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Desktop tests pin the clock so the weekday/date label is deterministic.
