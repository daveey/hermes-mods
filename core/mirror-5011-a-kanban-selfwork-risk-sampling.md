# Kanban self-work risk classification and stable sampling policy

Mirror snapshot 5011; ordinals 4438.

Entries:

- **4438**: feat(kanban): add conservative self-work risk and stable sampling policy

## Behavior

- Dispatcher, credentials, pacing/quota and money paths stay on independent review with mandatory verification. Ordinary tests and guard comments are recognized from complete patches, and other ordinary merged fixes are sampled by a stable seeded repository/SHA hash controlled by one reversible config key (documented in the configuration guide).
- These helpers alone do not authorize a merge; the shipping-state and sweep consumers land separately.

## How it works

`kanban_selfwork_risk.py` and `kanban_selfwork_sampling.py`, defaults in `config_defaults.py`.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/config_defaults.py`
- `hermes_cli/kanban_selfwork_risk.py`
- `hermes_cli/kanban_selfwork_sampling.py`
- `website/docs/user-guide/configuration.md`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/hermes_cli/test_kanban_selfwork_risk.py`
- `tests/hermes_cli/test_kanban_selfwork_sampling.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Conservative by design: ambiguous patches are treated as risky.
- The sample decision is deterministic for a given seed, so changing the seed changes the sampled set.
