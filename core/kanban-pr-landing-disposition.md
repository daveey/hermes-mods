# Kanban: PR completion requires a landing disposition

Patches:

- **1649** (`34fe22e1f1`, `fix(kanban): require PR landing disposition on completion`).
- **1656** (`243054ddac`, `fix(kanban): carry landing disposition through release, notices and payload`): review fixes.
- **1657** (`6a2f5928d8`) and **1658** (`b0465fabe5`): rebased duplicate source commits. Their source diffs are
  byte-identical to 1649 and 1656 (ignoring `index` lines), so they implement nothing new; the mirror carries them as
  separate SHA entries only (they may be empty mail entries in the replay).

Related: [kanban-park-invariants.md](kanban-park-invariants.md) (PR acceptance and `published_pr` binding),
[mirror-0916-fix-kanban-a-pr-contract-on-a-repo-with-no-required-checks-completes-via-metadata-con.md](mirror-0916-fix-kanban-a-pr-contract-on-a-repo-with-no-required-checks-completes-via-metadata-con.md) (`contract_waiver`).

## Behavior

- **An OPEN PR no longer completes on green CI alone.** Both the `kanban_complete` tool and `hermes kanban complete`
  used to complete a card whose PR was open with exact-head CI green. Completion of a PR contract, or of any card that
  passes `metadata.published_pr` (now including cards created `local-only`), now needs a disposition:
  - **MERGED**: accepted; exact-head required CI still applies.
  - **CLOSED** (without merging): accepted as a disposition, explicitly "not delivery"; CI is not required. (Before, it
    was the `closed_unmerged` refusal; a positively closed-unmerged binding can still be replaced by a new
    `published_pr`.)
  - **OPEN** with `metadata.landing_owner=<card id>`: the owner must be another card on the **same board**, in an open
    status, whose `completion_contract` is exactly this PR URL. Self, done, archived, missing and different-PR owners are
    refused. The owner is checked again inside the completion write transaction (no network I/O there), so an owner
    closed or rebound in between is caught.
  - **OPEN** with `metadata.pr_left_open={"reason": "<nonblank>"}`: accepted; the reason is retained on the completed
    event and in the done notice with the caveat "The hub batch only installs merged main; it never merges PRs."
  - Otherwise the receipt is `open_unlanded` with a recovery text (merge now with exact-head CI green and review comments
    answered, or create the landing card, or leave it open with a reason).
- `metadata.contract_waiver` still waives only absent required checks, never the landing gate; GitHub read errors stay
  fail-closed (infra), even with a left-open reason. A left-open exception does not bypass the CI gate.
- **1656 fixes.**
  - Origin sessions are released for every accepted disposition: `observe_owner_task` now treats an ok `closed` receipt
    like `success` (before, a CLOSED disposition never released them). A CLOSED receipt for a different PR does not.
  - The left-open notice is rendered by every formatter: the gateway notifier (1649), the TUI formatter
    `_kb_completed`, its `FINAL SEND` branch, and the remote completion text that reuses it.
  - A malformed `metadata.published_pr` (int, list, non-URL string) is refused as `malformed_published_pr` before any
    GitHub read, instead of an infra retry or a misleading contract mismatch.
  - The completed payload takes `pr_left_open` / `landing_owner` only from the recorded receipt's validated `landing`,
    so a MERGED PR or a local-only card cannot announce "left open" or store an arbitrary owner from raw metadata.
  - Guidance tells review-only cards to cite the reviewed PR in `metadata.pr` or `metadata.reviewed_pr`, never
    `published_pr` (which binds the card's contract permanently).

## How it works

`hermes_cli/kanban_pr_landing.py` (new): `left_open_reason`, `landing_disposition` (board-local SQLite read of the
owner row; open statuses are the default workflow minus `done`), `check_landing(conn, task_id, receipt, metadata)`
(only for `pr_state == "OPEN"`; sets `receipt["landing"]` or turns an ok/waived receipt into `open_unlanded`) and
`left_open_notice`. `kanban_pr_acceptance.py` records `pr_state` (MERGED/OPEN/CLOSED) on the receipt.
`kanban_pr_acceptance_store.prepare_acceptance` now also runs for local-only cards with `published_pr`, returns
`(snapshot, receipt, metadata)`, and calls `check_landing`; the write-transaction path calls it again.
`kanban_db_completion_payload.completed_event_payload(..., acceptance)` copies landing fields from the receipt.
Guidance is in `agent/prompt_builder.KANBAN_GUIDANCE`, `tools/kanban_tools_schemas.py` (`kanban_complete`) and
`website/docs/user-guide/features/kanban.md`.

## Code

`hermes_cli/kanban_pr_landing.py` (new), `hermes_cli/kanban_pr_acceptance.py`, `hermes_cli/kanban_pr_acceptance_store.py`,
`hermes_cli/kanban_db_completion_payload.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban_origin_ledger.py`,
`gateway/kanban_watchers_notifier.py`, `tui_gateway/session_notifications.py`, `agent/prompt_builder.py`,
`tools/kanban_tools_schemas.py`, `website/docs/user-guide/features/kanban.md`.

## Tests

Named from the patches; **not rerun by this mirror pass**. Every case marked "tool and CLI" runs through both the
registered `kanban_complete` tool and `hermes kanban complete` (`run_slash`), against an HTTP GitHub fixture; these
tests are marked `platforms("linux")`.

- 1649, new `tests/hermes_cli/test_kanban_pr_landing.py` (tool and CLI): `test_open_pr_cannot_complete_on_hourly_batch_promise`,
  `test_terminal_pr_disposition` (MERGED, CLOSED), `test_left_open_is_recorded_and_visible_in_done_notice`,
  `test_landing_owner_must_be_open_same_board_exact_pr` (ready / done / archived / self / wrong_pr / missing),
  `test_owner_changes_after_prepare_are_rechecked_in_write_transaction` (done / archived / wrong_pr),
  `test_no_landing_bypasses` (local publication, waiver, blank and string reasons, read error),
  `test_left_open_exception_preserves_ci_gate` (failure / pending / missing / stale / no_required),
  `test_owner_on_other_board_is_not_a_landing_owner`. Updated: `test_kanban_pr_acceptance.py` (fixture PR state defaults
  to MERGED), `test_kanban_park_invariants.py` and `test_kanban_pr_wake.py` (CLOSED is now an ok `closed` receipt).
- 1656, new `tests/hermes_cli/test_kanban_pr_landing_notices.py`: `test_every_accepted_disposition_releases_origin`
  (merged / closed / open+left_open / open+landing_owner), `test_closed_receipt_on_other_pr_does_not_release_origin`,
  `test_left_open_in_local_gateway_notice`, `test_left_open_in_tui_notice`, `test_left_open_in_tui_final_send_notice`,
  `test_left_open_in_remote_notice` (plain and FINAL SEND), `test_merged_payload_ignores_raw_landing_metadata`,
  `test_local_only_payload_ignores_raw_landing_metadata`, `test_open_payload_records_validated_landing_owner`,
  `test_malformed_published_pr_is_its_own_refusal` (tool and CLI; int / list / string; local-only and repo contracts;
  asserts no GitHub read), `test_guidance_steers_review_only_cards_away_from_published_pr`.

Untested: the dashboard/REST completion path, live GitHub, Desktop rendering of the notice, and non-Linux runs of these
tests.

## Applying

Apply 1649 then 1656 in series order; 1657 and 1658 follow as duplicates of those two and add no change. They depend on
the PR-acceptance store and receipts from the earlier series. No new config.

## Gotchas

- A review/release child created up front is not a landing owner unless its `completion_contract` is the exact PR URL.
- A card completed with `published_pr` is bound to that PR even if it was created `local-only`; review cards must use
  `metadata.pr` / `metadata.reviewed_pr`.
- CLOSED completes the card but means the change did not ship through that PR.
- The caveat text names the hub batch literally; it is fixed product text, not configuration.

## Plugin?

No. The gate sits in the core completion transaction shared by the tool and CLI.
