# Nightly E2E dispatch and actionable reports

Patches: 1354 (`5c77befe865`), 1355 (`a87b03cf637`),
1356 (`4b080e37087`), 1357 (`9722d7272fa`),
1430 (`7737f2331c81`), 1432 (`8abb9e18b42b`).

## Behavior

- A dispatch-only workflow runs the reusable upstream-sync E2E job with Python lanes and E2E-upgrade disabled; Python defaults on for other callers.
- Dispatch records the resolved SHA and newly observed run. Since 1430, reporting keeps **one open red card per alert intent**, not one per exact failing-test set. Production main, other refs and acceptance markers have separate scopes. Subset, superset and disjoint failures update the same open owner with newly failing, no longer failing and still failing IDs.
- A red run after the owner is done or archived creates the next generation. Other statuses remain open; an unreadable status prevents replacement and keeps the run retryable.
- Green runs comment on open cards in their scope without closing them automatically. A successful green comment clears that red card's failing baseline; a failed comment does not.
- Missing runs after 30 minutes, unfinished runs after four hours, cancelled runs and persistent lookup/log errors past four hours use a separate did-not-run finding. They do not replace the red baseline. Board failure retains pending work and makes `report` exit 1.
- Concurrent report/dispatch processes serialize state changes without losing a dispatch.
- 1432 adds an advisory for an exported reporter whose `nightly_e2e.py` or sibling `flaky_kanban.py` differs from local `origin/main` and whose source file changed more than two hours ago. It files a separate deployment-drift card, not a nightly-failure owner. Pin-check failure logs a line and still settles pending runs.

## How it works

`scripts/ci/nightly_e2e.py` exposes `dispatch` and `report`, with `--ref`,
`--repo`, `--workflow-ref`, `--state`, `--hermes`, and `--core` options.
State defaults to `~/.hermes/state/nightly-e2e.json`; an exclusive file lock covers load through atomic save. Reads accept UTF-8 with optional BOM; BOM-specific behavior is untested here. GitHub calls use `GH_TOKEN` or `GITHUB_TOKEN`; board operations use the CLI adapter.

The reporter parses pytest `FAILED` and `ERROR` summary IDs, ignoring live copies. Failures outside pytest use a diagnostic placeholder. `_report_red` stores the latest sorted IDs and creation idempotency key under the scoped `red` record only after the board write succeeds. `_report_green` records which run was commented so retries after a later card's failure do not repeat already-recorded green comments.

1430 migrates legacy `failed-<fingerprint>` records on the next red report in each scope. It prefers an open exact-fingerprint owner, otherwise the first by key. Other open legacy owners receive a superseded comment, not automatic closure. A baseline is known only when the current IDs match the old fingerprint or a complete body list hashes to it; otherwise the comment explicitly labels the baseline unknown. Migration preserves the original creation key.

The same patch updates upstream-sync diagnostic owner lookup: the red record's latest set is authoritative, not the initial card body. Main scope, open status and reporter provenance remain required, including exact creation-key validation when the board supplies it. See [REFUSED diagnostics](upstream-sync-refused-diagnostics.md).

`check_pin` skips an in-checkout reporter. For exported copies it freezes local `origin/main` to one SHA, compares raw file SHA-256 digests and reads each file's last-change commit time. It never fetches during the tick. `pin_cards` keys contain both source blob IDs; unrelated main advances can update an open card's body without creating another card for the same blobs. This state is separate from run cards, so a green run cannot imply deployment drift was repaired.

## Code

- `.github/workflows/nightly-e2e.yml`, `.github/workflows/tests.yml`.
- `scripts/ci/nightly_e2e.py`: `_is_open`, `_adopt_legacy`, `_report_red`, `_report_green`, `check_pin`, `report`.
- `scripts/ci/upstream_sync_diagnostics.py`: `_owner_identity`, `nightly_owner` (1430).
- `tests/ci/test_nightly_e2e.py`, `tests/ci/test_nightly_e2e_red_cards.py`, `tests/ci/test_nightly_e2e_pin.py`.
- `tests/scripts/ci/test_upstream_sync_diagnostics.py`.

## Tests

Source coverage inspected; **source tests were not rerun in this mirror pass**. No live GitHub, board or scheduler delivery was exercised.

- `tests/ci/test_nightly_e2e.py`: workflow/default-Python checks; distinct dispatch adoption; sorted unique FAILED/ERROR IDs; same/different/subset red updates; archived replacement; green settlement; missing/cancelled/deadline cases; failure outside pytest; persistent GitHub/board errors; CLI exit/pending state; concurrent process locking and state round-trip.
- 1430 `tests/ci/test_nightly_e2e_red_cards.py`: `test_any_later_red_comments_on_the_one_open_card_with_exact_delta`, `test_latest_failing_ids_are_the_next_baseline`, `test_red_after_the_card_is_closed_files_the_next_generation`, and `test_main_historical_and_acceptance_scopes_keep_separate_red_cards` cover scope and ownership. Did-not-run separation, green baseline reset, unreadable owner retry, failed red/green comments, legacy body/fingerprint recovery and unknown baselines, duplicate migration, migration failures, CLI state persistence and board failures beyond the deadline have explicit cases in that file.
- 1430 `tests/scripts/ci/test_upstream_sync_diagnostics.py`: `test_red_owner_uses_latest_set_not_initial_body`, `test_red_owner_keeps_adopted_legacy_identity`, `test_red_owner_rejects_unrelated_provenance`, `test_acceptance_record_is_not_main_owner`, `test_saved_red_identity_cannot_authorize_an_unrelated_card`, and `test_red_latest_set_is_authoritative_over_stale_legacy_record`.
- 1432 `tests/ci/test_nightly_e2e_pin.py` uses temporary real Git repositories with fake GitHub/board adapters: identical copies stay silent; either stale file files once across two reports; changes at or below two hours do not file; pin-check exceptions and missing Git refs still allow run settlement; in-checkout execution skips the check; unrelated main advances refresh only the existing card; CLI `--core` forwarding is exercised.

## Applying

Use the complete numbered, bridge-aware replay procedure in [README.md](README.md), including predecessors before 1430 and 1432. These patches are not a standalone bundle. Provision dispatch/report scheduling on an always-on Linux host and the required Actions/board access separately; no cron jobs are installed by these patches. Pass `--repo` for the adopter's fork.

For an immutable reporter deployment, export **both** `scripts/ci/nightly_e2e.py` and `scripts/ci/flaky_kanban.py` from the same frozen source SHA and point both dispatch/report wrappers at that directory. `--core` identifies the checkout supplying local `origin/main` for the advisory. Applying the source does not update already-pinned exports.

## Gotchas

- There is no GitHub `schedule` trigger. Most tests use fake transports; the pin tests exercise local Git, not a deployed scheduler.
- The freshness advisory depends on a separately refreshed local `origin/main`, compares bytes (including line endings), and uses source commit time, not deployment time.
- Legacy duplicates are commented as superseded, not automatically archived. Unknown or truncated legacy lists do not justify invented deltas.
- Pin records are deduplicated by source blobs; the check does not automatically replace a closed drift card for unchanged blobs.
- Green is evidence about a run, not permission to auto-close repair or deployment work.

## Plugin?

No. These are repository CI scripts and workflows using the existing Kanban CLI adapter.
