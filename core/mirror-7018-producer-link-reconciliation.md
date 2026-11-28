# Producer chat-link reconciliation without display feedback

Patch coverage: 6713, 6714, 6715, 6716, 6717, 6718, 6719.

## Behavior

Chat links between producer cards and chats are reconciled from raw producer closure rather than from display state, so display feedback cannot feed back into link writes. Finished producer rows authorize closure of *existing* links only: they do not backfill historical links or discover history-only boards. Closed observations are retained in existing-board signatures and owner-written release receipts keep their separate protocol. Reconcile also skips a candidate link effect (create, release, reopen, machine link) when the task's containment custody is held (`writable(task_id)`, checked under the writer lock) so a rejected containment write is not restored by the next pull; this is write eligibility, not custody release, and marker-only or malformed custody stays held. Derived `released` is `not unfinished` for raw origin rows, copied rather than mutating shared reader rows. Link-only board discovery (`ensure_link_boards`) is limited to boards that have at least one unfinished ledger row; closed rows still invalidate existing-board signatures.

## How it works

`hermes_cli/kanban_chat_links.py` gains `reconciliation_cards` and `writable` and reconciles producer links from raw closure; `kanban_sync_client.py` is adjusted for the cache inputs. Commit 6719 only trims a docstring to stay inside the facade line budget.

## Code

`hermes_cli/kanban_chat_links.py`, `hermes_cli/kanban_sync_client.py`.

## Tests

Read from source; **not executed here**. `test_kanban_chat_links.py`, `..._containment.py`, `..._oscillation.py`, `..._owner_hub_subs.py`, `..._parity.py`, `test_kanban_ledger_links_bulk.py`, `test_kanban_sync_steady*`. `test_reconcile_containment_write_branches` (6717) exercises the containment write branches of `reconcile`; link writers outside `reconcile` and these suites are **untested**, and "every writer" is not claimed.

## Applying

Apply in order; 6714-6717 migrate fixtures to the producer view introduced in 6713.

## Gotchas

- Do not widen closure to history-only boards.

## Why core, not a plugin?

Kanban chat-link internals.
