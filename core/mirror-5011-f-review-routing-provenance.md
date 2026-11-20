# Review routing provenance, declared implementers and replica sources

Mirror snapshot 5011; ordinals 4877, 4878, 4926, 4927, 4950, 4951, 4952.

Entries:

- **4877**: Fail closed on missing downstream reviewer provenance
- **4878**: Exercise missing review evidence after pool pickup
- **4926**: Review routing: a downstream review card may declare its implementer models
- **4927**: Review routing: a declared implementer must be a model the routing registry knows
- **4950**: Resolve evicted review sources from the board replica
- **4951**: Prove review replica snapshot lifecycle and failure cleanup
- **4952**: docs(kanban): clarify translated review evidence failures

## Behavior

- **Fail closed** (4877–4878): a downstream review without implementer evidence is refused; missing or partial source cards never spawn an implementer pin. Intact mandatory and non-mandatory pins are unchanged.
- **Declared implementers** (4926–4927): a downstream review body may carry one `<!-- implementer-models: a, b -->` line. Concrete model ids only. The whole declaration is void unless every name is known to the assignee's routing registry. A declaration on a non-review card is ignored. The reviewer is never a declared implementer.
- **Replica sources** (4950–4951): absent source cards and their run/event evidence are read from one read-only selected-board replica snapshot. Local source evidence wins. Missing or unreadable replica stays fail-closed. Unsynced boards never open a replica. Snapshot reuse/close and SQLite read errors are covered.
- **Docs** (4952): paced reroute comments that review evidence reads translate storage errors to `ReviewModelUnavailable`, so a failed peer probe keeps the already-resolved independent base route. Untested (comment only).

## How it works

- `hermes_cli/kanban_review_model.py` (`declared_implementers`, `resolve_review_model`).
- `hermes_cli/kanban_review_sources.py` for replica snapshot reads.
- `hermes_cli/kanban_paced_reroute.py` docstring only in 4952.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_paced_reroute.py`
- `hermes_cli/kanban_review_model.py`
- `hermes_cli/kanban_review_sources.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_mandatory_pin.py`
- `tests/hermes_cli/test_kanban_review_declared_implementers.py`
- `tests/hermes_cli/test_kanban_review_pin_admission.py`
- `tests/hermes_cli/test_kanban_review_source_replica.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- Unknown names that look like model ids used to lift the fail-closed refusal; they now void the declaration.
- Replica reads never import a runnable source card or rewrite pins.

## Plugin?

None.
