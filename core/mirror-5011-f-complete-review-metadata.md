# Completion tool schema: review and landing metadata

Mirror snapshot 5011; ordinals 4884, 4885.

Entries:

- **4884**: Declare kanban completion review and landing metadata
- **4885**: Restore description-only review metadata compatibility

## Behavior

- The complete-tool schema advertises the documented review and landing keys (`verdict`, `reviewed_sha`, `reviewed_pr`, findings and related contract keys) so schema-guided callers emit them instead of only `completion_receipt`.
- Free-form metadata remains allowed. Handlers and ship gates stay authoritative; the schema does not impose new transport constraints.
- 4885 restores description-only review metadata compatibility after a stale worker publication.

## How it works

- `tools/kanban_tools_schemas.py` declares the keys on the complete tool. Schema budget increases for contract discoverability.

## Code

Shipped non-test paths (union over these entries):

- `tools/kanban_tools_schemas.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/tools/test_kanban_complete_receipt_entry_paths.py`
- `tests/tools/test_kanban_complete_receipt_schema.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- Declaring keys in the schema does not validate or land a PR. Completion gates still enforce landing disposition separately.

## Plugin?

None.
