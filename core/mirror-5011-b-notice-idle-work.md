# Indexed notice discovery and idle-pass gates

Mirror entries: 4512, 4515, 4516, 4517, 4518, 4550.

## Behavior

- **4512** — Hosted owner discovery uses a binary indexed prefix range instead of a case-insensitive broad metadata LIKE scan.
- **4515** — Notice-guarantee discovery can start from a partial kanban-notice message index while preserving legacy fallback.
- **4516** — Orphan subscriptions and settled remote ledger rows stop holding unloaded notice work perpetually hot.
- **4517** — Index detection uses PRAGMA index_list and gate regressions track the actual indexed/orphan/settled behavior.
- **4518** — Card-change passes skip bookkeeping-only local changes and trusted unchanged remote inputs, while observing first-seen terminal cards.
- **4550** — Remote memo keys digest relevant ledger content instead of comparing republished card-object identities.

## How it works

Hosted owner reads use the canonical fixed lowercase ownership prefix's binary primary-key range. Suffix bytes and superclass ordering remain unchanged; case variants admitted by LIKE never conferred authorization and are no longer candidate rows.

Notice discovery uses `idx_messages_kanban_notice` to find each session's latest active notice, then checks it is still the session's latest active message and applies archive/mute/end-reason filters. Older or unreconciled stores without the index keep the exact legacy query; explicitly selected chats keep their existing direct lookup.

An orphan subscription with no chat in any profile store, a nonarchived card and no inflight claim is part of the gate key rather than perpetual pending work. Archived cards, new chats, inflight claims and unreadable inputs still run. Remote gating requires every owed notice to be settled, includes ledger revision and task-row signatures, and still observes unlinked terminal arrivals so later links cannot resurrect expired outcomes.

Local fanout can advance its event cursor over bookkeeping-only changes. Remote card-change memoization requires trustworthy replica content revisions and an acknowledged pass. Ledger keys sort and digest only fields used by the projection/pass, so identical asynchronous re-projections compare equal while changes to ownership, status, chat or origin identity invalidate the memo.

## Code

- `gateway/session_hosted_service.py`
- `hermes_state_common.py`
- `tui_gateway/kanban_unloaded_gates.py`
- `tui_gateway/notice_fanout.py`
- `tui_gateway/notice_guarantee.py`

## Tests

Source tests compare indexed and legacy candidate results and query plans, missing-index fallback, canonical owner suffix/order/profile behavior and VM work. Gate suites cover orphan/chat appearance, archived/inflight/failure cases, every owed remote settlement, first-seen unlinked outcomes, bookkeeping cursor movement, changed statuses, unvouched inputs and failed-refresh retries. The final memo tests use the real ledger refresh path across repeated 30-second advances rather than mocking only the key builder.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/gateway/test_hosted_owner_prefix.py`
- `tests/tui_gateway/test_h2_gate_mutations.py`
- `tests/tui_gateway/test_h2_remainder_c11.py`
- `tests/tui_gateway/test_kanban_unloaded_gates.py`
- `tests/tui_gateway/test_notice_candidates_index.py`
- `tests/tui_gateway/test_notice_guarantee_idle.py`
- `tests/tui_gateway/test_remote_card_outcomes.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

Unproven or unreadable inputs stay hot; a failed pass must not become a cached success. Object identity is not a reliable change detector when refresh republishes equivalent dictionaries. No polling intervals or delivery deadlines are relaxed, and historical CPU or scale claims are not freshly certified here.
