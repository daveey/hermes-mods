# Kanban ending tools: explicit scratch source at initial attachment

Mirror snapshot 5406; ordinals 5342, 5343, 5344.

Entries:

- **5342**: Allow explicit scratch source initial attachment at ending tools
- **5343**: Keep ending source schema within existing prompt budget
- **5344**: Prove other-card move refuses explicit source attachment

## Behavior

- **5342**: the dispatcher-bound registration and receipt pipeline is reused for the six ending tools; the source attaches before artifact staging, preserving pending observations, immutable identities and implementation-only claims. Capability-routing combinations are rejected rather than silently dropping the explicit source; replays validate identity without resending a revision-zero attach.
- **5343**: tool-schema wording compressed to stay inside the existing prompt byte budget.
- **5344**: test that another-card move refuses an explicit source attachment while the omitted-field control passes.

## How it works

- `kanban_source_registration.py`, `tools/kanban_tools.py`, `kanban_tools_park_args.py`, `kanban_tools_schemas.py`, `kanban_tools_source.py` and the kanban user guide.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_source_registration.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_park_args.py`
- `tools/kanban_tools_schemas.py`
- `tools/kanban_tools_source.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/tools/test_kanban_source_ending_initial.py`
- `tests/tools/test_kanban_creator_repin.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Schema growth is capped by a byte-budget test; extra schema text will fail CI.
- Creator re-pin to another card refuses an explicit source.

## Why core instead of a plugin

Tool schemas and the receipt pipeline are core agent tooling.
