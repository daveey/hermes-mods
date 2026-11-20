# Opt-in queue-fairness evidence capture

Mirror snapshot 5011; ordinals 4929.

Entries:

- **4929**: feat(kanban): opt-in queue-fairness evidence capture and live report

## Behavior

- Operator-armed capture on one Kanban database: bounded SQLite change journal (`kanban_fairness_*` tables and triggers plus an opening snapshot, one transaction), drain to hashed JSONL, seal with a manifest, report cohorts and four gates with interval bounds.
- A gate passes only on its upper bound, fails only on its lower bound, and is INDETERMINATE for an empty cohort, unbounded episode, dropped change, missing seal, replaced journal or missing owner history.
- Inventory lists named-tree producers and alarm keys and refuses an unclassified match.
- The dispatcher does not read the journal. Nothing happens until an operator arms it.

## How it works

- `scripts/kanban_fairness.py` plus `hermes_cli/kanban_fairness_*.py`. Admission score is used only to classify cohorts, not to change ordering (Phase B is not in this entry).

## Code

Shipped non-test paths (union over these entries):

- `docs/kanban-fairness-capture.md`
- `hermes_cli/kanban_admission_score.py`
- `hermes_cli/kanban_fairness_inventory.py`
- `hermes_cli/kanban_fairness_journal.py`
- `hermes_cli/kanban_fairness_metrics.py`
- `hermes_cli/kanban_fairness_schema.py`
- `hermes_cli/kanban_fairness_store.py`
- `scripts/kanban_fairness.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_fairness_inventory.py`
- `tests/hermes_cli/test_kanban_fairness_journal.py`
- `tests/hermes_cli/test_kanban_fairness_metrics.py`
- `tests/hermes_cli/test_kanban_fairness_vertical.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- Opening backlog larger than the bound is a loss, not a pass. Requeue between sightings is two episodes. Priority cohort is fixed at ready entry.

## Plugin?

None.
