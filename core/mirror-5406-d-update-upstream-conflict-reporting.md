# Update: configured integration owner for upstream conflict reports

Mirror snapshot 5406; ordinals 5404, 5405.

Entries:

- **5404**: fix(update): consolidate conflict observations on configured integration owner
- **5405**: fix(update): register conflict reporting subprocess and CI dependencies

## Behavior

- **5404**: opt-in `update.upstream_conflict_reporting` requires exactly `mode`, a gateway-qualified `owner`, canonical HTTPS `repository` and absolute `owner_checkout`. Unset/null retains legacy per-checkout owner creation/reuse. Malformed config, a mismatched repository, or invalid/unreachable authoritative owner refuses reporting rather than falling back to a new owner; the outer reporter surfaces a warning.
- `inventory` appends full SHA/path observations only to the designated existing local owner, on the bound checkout, authenticated gateway and board. `pointer` appends compact owner/SHA/checkout-incident pointers only to existing nonterminal local consumers, excludes foreign pickups and archived rows, and never includes the path inventory. Identical comments are serialized/deduplicated; changed SHAs can add a new comment. With no eligible consumer it returns the validated owner's ID without creating a task.
- Reporting never creates, reopens or redirects configured tasks, releases containment, or changes acceptance/update fences. Historical inventories are not removed. Owner validation uses fresh authenticated Board row evidence rather than a cached replica.
- **5405**: reporter subprocess and CI dependencies are registered.

## How it works

- `update_conflict_reporting.py`, `update_upstream_conflict.py`, config files, spawn inventory, CI classifier, configuration doc.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/config.py`
- `hermes_cli/config_defaults.py`
- `hermes_cli/config_value_checks.py`
- `hermes_cli/update_conflict_reporting.py`
- `hermes_cli/update_upstream_conflict.py`
- `website/docs/user-guide/configuration.md`
- `hermes_cli/worker_drain_spawn_inventory.json`
- `scripts/ci/classify_changes.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/hermes_cli/test_update_conflict_reporting.py` contains pointer/inventory targeting and dedupe cases, concurrent pointer insertion, malformed config and owner-evidence refusals, foreign/archived consumer exclusion and separate checkout incident identities. A fresh-child test uses a local HTTP owner fixture and temporary Git checkout to check config rereading and unchanged source refs; it is not a live Board or deployed-updater test.

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Does not integrate upstream.

## Why core instead of a plugin

Update flow and config validation are core.
