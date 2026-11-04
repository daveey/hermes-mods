# Runtime: skipped worker receipt sequences named on both sides

Patches: **1910** (`9825be1856`, `[daveey] fix(runtime): name the skipped worker receipt sequence on both sides`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- A worker receipt whose sequence is not `last_sequence + 1` is refused with `field='sequence'`, `detail='expected N, got M'` (wire reason still `invalid_params`), so the owner logs it at WARNING and the worker logs the named refusal before raising. Diagnostic only: the cause of the lost receipt is not fixed.

## How it works

See Code.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1910: Run 36008 (card t_01a1a264e0987946) and its sibling worker died at the same second (01:11Z) with a sticky WorkerPersistenceError: invalid_params. Evidence: the worker journal (~/.hermes/worker-outboxes/admission-worker-4af4aa9b…/pending.json) held sequence 194, state.db worker_executions.last_sequence was 192; the sibling's journal 171 vs owner 168. Not disk, not locks: no busy/storage errors in agent/gateway/errors logs in that window.

## Code

`agent/runtime_session_store.py`, `hermes_state_runtime.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/agent/test_worker_receipt_sequence_diag.py`: `test_owner_names_the_expected_sequence`, `test_gateway_logs_named_sequence_refusal_at_warning`, `test_worker_logs_named_refusal_and_keeps_bare_reason`, `test_worker_bare_refusal_logs_nothing_new`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
