# Independent local and relayed credential disable policy

Mirror batch 6; ordinals 4030, 4031, 4032.

## Behavior

Local enable/remove operations preserve relay-owned disable records. Local disable remains independent of a concurrent relay disable, and CLI enable reports when relay policy still disables a pulled credential.

## How it works

Disable keys carry optional source scope. Relay records scoped to manual:hub_minted may match generic transport labels but not an independently granted local credential. Local setters and removal cleanup exclude hub-oauth-relay records. Reporting reads active/root stores for owned matching records and emits a still-disabled explanation instead of an enabled success message.

## Code

Source paths touched by this feature group (including regression fixtures):

- `agent/credential_pool_disabled.py`
- `hermes_cli/auth_commands.py`
- `tests/agent/test_credential_disabled_relay.py`
- `tests/hermes_cli/test_auth_disable_command.py`

Mail entries in the ordered series:

- 4030: [`607a1e2fe59bdd065f7b42305e7fc60282bc580b`](patches/4030-daveey-Preserve-independent-local-and-relay-credential-disable-policy.patch)
- 4031: [`a4d578cc4e6136d7a5dc48a1ef3e28b1fd99103a`](patches/4031-daveey-Report-remaining-relay-policy-after-local-auth-enable.patch)
- 4032: [`8653f9907e79284107d4d989b5671d1b3ae71c66`](patches/4032-daveey-Cover-relay-reporting-ownership-scope-and-unreadable-roots.patch)

## Tests

- `tests/agent/test_credential_disabled_relay.py`: scoped generic/account labels match pulled rows only; local and remote records coexist; removing remote policy preserves local selection refusal; local enable/remove never deletes relay policy.
- `tests/hermes_cli/test_auth_disable_command.py`: active/root relay records with or without local disable produce still-disabled output and remain effective. Missing, malformed or empty root data yields no relay report, while non-owned, mismatched-label or wrong-source-scope records do not falsely report relay ownership.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

Local enable does not override relay policy: change the authoritative policy then pull again. Unreadable roots are ignored by the reporting helper; that behavior alone is not a general credential-admission guarantee.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.
