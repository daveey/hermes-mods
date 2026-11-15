# CI flaky tests: one daily intake card per repository

Mirror snapshot 4352; ordinals 4196, 4235.

Entries:

- **4196**: ci: batch unowned CI flakes into one durable daily intake card
- **4235**: ci: fix live daily-intake delivery (expose idempotency_key; reopen --reason)

## Behavior

The CI flaky-test producer used to create a card per first flake of each test id; this fanned out dozens of cards in one day. Now new unowned flake occurrences go into a durable SQLite queue and each repository gets one intake card per UTC day (idempotency key `ci-flaky-intake:<owner/repo>:<YYYY-MM-DD>`). Immutable, size-bounded JSON manifest pages give every occurrence a `needs_triage` status. Existing open exact-test/job owners, local or foreign, still receive deduplicated comments with readback and are never reassigned or archived. Red-main, retry, grace and threshold policy are unchanged.

- The queue needs an explicit one-time `--initialize-intake`, with a SQLite UUID plus a separately retained identity anchor; normal runs never auto-create it, so a lost or replaced queue fails closed.
- Pages are committed before any external write; ambiguous create/comment attempts stay immutable until an exact readback decides delivery; FIFO tickets rotate retries; a drain runs before discovery with half the budget; HTTP has an absolute deadline across retries, pagination and signed artifact hops. A done intake is reopened; an archived intake carries forward to the next UTC day. A clipped closed candidate no longer strands a row: it is admitted with an `owner_check` note. Journal mode retires a stored run that stays unavailable once a successful listing omits it and it reached the failed threshold.
- 4235 fixes live delivery: `hermes kanban show/list --json` now emits `idempotency_key` (which also re-enables `deflake:<id>` matching), and the CLI board adapter's reopen passes the required `--reason`.

## How it works

- `scripts/ci/flaky_intake.py` (queue, pages, delivery), `flaky_kanban.py` (board adapter), `flaky_artifact.py` / `flaky_artifact_poll.py` (metadata artifacts), `flaky_http.py` (deadlines). `hermes_cli/kanban_output.py` adds `idempotency_key` to the task JSON fields. `docs/ci-flaky-intake.md` documents the ownership coverage contract and backlog export.

## Code

Shipped non-test paths (union over these entries):

- `docs/ci-flaky-intake.md`
- `hermes_cli/kanban_output.py`
- `scripts/ci/flaky_artifact.py`
- `scripts/ci/flaky_artifact_poll.py`
- `scripts/ci/flaky_http.py`
- `scripts/ci/flaky_intake.py`
- `scripts/ci/flaky_kanban.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/ci/test_flaky_artifact_poll.py`
- `tests/ci/test_flaky_daily_intake_cli.py`
- `tests/ci/test_flaky_exit_policy.py`
- `tests/ci/test_flaky_http_deadlines.py`
- `tests/ci/test_flaky_intake.py`
- `tests/ci/test_flaky_intake_fairness.py`
- `tests/ci/test_flaky_intake_freshness.py`
- `tests/ci/test_flaky_intake_real_board.py`
- `tests/ci/test_flaky_intake_recovery.py`
- `tests/ci/test_flaky_kanban_github_retry.py`
- `tests/ci/test_flaky_kanban_overlap.py`
- `tests/ci/test_flaky_poll_budget.py`
- `tests/ci/test_flaky_protocol_boundaries.py`
- `tests/ci/test_flaky_run_retirement.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- No-match absence is checked against the local board plus fresh replica scopes; it is not a global certificate and unsynced legacy boards are not inventoried. The intake card owns final dedupe.
- Keep the queue file and its identity anchor until every undelivered row has an owner. Rollback: reverting leaves the old per-test producer ignoring the queue.
- No cost savings are claimed beyond a read-only historical replay.

## Plugin?

None.
