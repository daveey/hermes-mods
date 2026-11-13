# Kanban worker-first self-check repin refusals

Patches: **3244, 3251, 3279**.

## Behavior
- A denied card's self-check reads the same verdict the claim screen gates on (`pickup.verdict`: the hub-admission event, else the creation payload's audit), so a card whose only verdict is in its created payload is re-placed too. A structurally kept card skips the ready-since history read.
- A transient Board error (5xx, 408/425/429) on a self-check repin no longer disables the repin: it is retried next tick instead of sending the stranded card back to the reserve.
- A persistent refusal is remembered at that revision: any 4xx except 408/425/429, coded or not (for example an HTML 404/405 from a Board or proxy without the route), and a 501. A coded forbidden refusal still reserves the card at the refused revision; `not_claimable` keeps its own backoff.
- `kanban.placement.portable_wake_probes` is documented in the configuration guide and the docs-drift check now scans the claim module's `cfg.get(...)` reads.

## How it works
- 3244 switches `denied_target` to `pickup.verdict`, skips the history read for kept cards, and extends `scripts/ci/check_docs_drift.py`.
- 3251 restricts `_remember_selfcheck_refusal` to coded 4xx refusals; 3279 widens it to uncoded persistent 4xx and 501.

## Code
- `hermes_cli/kanban_worker_first_selfcheck.py`
- `hermes_cli/kanban_worker_placement.py`
- `scripts/ci/check_docs_drift.py`
- `website/docs/user-guide/configuration.md`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_claim_deny_selfcheck.py`: `test_self_check_repins_a_card_whose_only_verdict_is_in_its_created_payload`, `test_a_structurally_kept_card_skips_the_ready_since_read`.
- `tests/hermes_cli/test_kanban_pool_deny_runs_on.py`: `test_a_transient_board_error_on_a_self_check_repin_is_retried_next_tick`, `test_a_forbidden_self_check_repin_still_reserves_at_the_refused_revision`, `test_an_uncoded_persistent_refusal_of_a_self_check_repin_reserves`, `test_an_uncoded_retryable_4xx_on_a_self_check_repin_is_retried`.
- `tests/ci/test_docs_drift.py`: two assertions added by 3244 (no new test names).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A refusal is remembered per card revision; it is retried only when the card's `sync_version` moves or the process restarts.
