# Relay-minted revoked 401: re-pull and adopt the pushed token before rotating

Patches: 2839, 2840.

- **2839** (`2e6de36904`, relay-minted 401 revoked: re-pull and adopt the pushed token before rotating (a hub re-mint no longer gives Kanban cards up))
- **2840** (`b2a53ee6b8`, relay re-pull: a Mac kickstarts its launchd hub-oauth-pull job (the 15:30Z burst hit the MacBook and mini too))

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- `credential_relay_repull` recovers a hub-minted (`manual:hub_minted`) relay row whose 401 body says revoked: it runs the host's pull command (`oauth_relay.pull_command`, else the worker pull script) at most every 10 s with a 60 s timeout, polls until the failing row holds a new token (wait budget `repull_wait_s`, default 120 s), and swaps to it; a re-pulled token refused again is still an auth failure; a re-mint revoking every account re-pulls and continues the turn. Other 401s fall through. `credential_auth_recovery` calls it before rotation.
- 2840: a Mac kickstarts its launchd hub-oauth-pull job; a worker node runs its pull script for the default home only.

## How it works

- `agent/credential_relay_repull.py` (new), `agent/credential_auth_recovery.py`.

## Code

- `agent/credential_auth_recovery.py`
- `agent/credential_relay_repull.py`

## Tests

- `tests/agent/test_credential_relay_repull.py` (revoked minted 401 repulls and the turn succeeds; re-pulled token refused again; only revoked 401s on relay rows; one re-mint revoking all accounts; Mac launchd kickstart; worker default-home only), `tests/hermes_cli/test_kanban_pool_auth_wall.py`.

Test files touched by these patches:

- `tests/agent/test_credential_relay_repull.py`
- `tests/hermes_cli/test_kanban_pool_auth_wall.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Host pull commands are deployment-specific and not part of the patch; an unconfigured host simply falls through.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.
