# Replica rows are decoded once per distinct stored text

Patch **0557** (source `5ca5255515`).

## Behavior

The remote-links projection behind the sidebar's Kanban badges rebuilds whenever `kanban.db` or `hub.db` changes, which on a
busy host is every sidebar refresh. Each rebuild re-parsed every replica row and re-hashed it twice (legacy record digest,
owner-evidence `task_hash`): about 0.2 s of JSON work per rebuild on a 6k-row `hub.db`. Now the per-row decode is memoized.
On the live replica a rebuild dropped from 0.29 s to 0.09 s with identical cards.

## How it works

`hermes_cli/kanban_session_activity.py::_read_remote_links` memoizes (task, legacy digest, owner evidence) keyed on the row's
exact stored text. The memo is replaced by the current rows on every read, so it stays bounded and an edited row always
decodes afresh. `_owner_attests_terminal` takes the precomputed evidence instead of recomputing it.

## Code

`hermes_cli/kanban_session_activity.py`.

## Tests

`tests/hermes_cli/test_kanban_replica_decode_memo.py`: `test_unchanged_replica_is_not_decoded_again`,
`test_edited_row_is_decoded_again_and_changes_the_answer`, `test_tampered_row_never_reuses_its_old_evidence`.

## Applying

`git am -3`; needs the replica/owner-evidence patches already in the series.

## Gotchas

The memo key is the stored text, not the row id: do not key it by id or an edited row would keep stale evidence.
