# Quota: revoked-401 churn is not a quota wall; `quota-release --probe`

Patches: 2790, 2791.

- **2790** (`878abd7c58`, test(kanban): auth-churn quota RED tests)
- **2791** (`4dd02e7ef5`, fix(kanban): revoked-401 churn never types a quota reset; quota-release --probe refuses a confirmed wall)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- A pool emptied by auth failures raises `SubscriptionAuthUnavailable`: exit 75, receipt `quota_unconfirmed`, a short capped backoff (max 600 s), and no quota episode or provider wall. A start-time `SubscriptionQuotaUnavailable` that the live `/api/oauth/usage` read contradicts is booked as `QuotaContradicted` through the same unconfirmed path.
- `hermes kanban quota-release <id> --probe` releases only when the live usage read shows headroom on every walled provider; otherwise it records an audited `quota_release_refused` event and exits 2. A new wall after a probe release holds again.
- 2790 is the RED test set; 2791 is the implementation.

## How it works

- `agent/model_routing_runtime.py`, `agent/model_routing_unavailable.py` (auth_churned / auth_benched), `gateway/session_kanban.py`, `hermes_cli/turn_exit.py` and `kanban_quota_receipt.py` (unconfirmed receipt) implement the churn path; `kanban_quota_probe.py` (usage_verdict, live_verdict, 25 s deadline), `kanban_quota_release.py`, `kanban_quota_episode_release.py`, `kanban_provider_walls.py` and `kanban_parser.py` (`--probe`) implement the probe release.

## Code

- `agent/model_routing_runtime.py`
- `agent/model_routing_unavailable.py`
- `gateway/session_kanban.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_provider_walls.py`
- `hermes_cli/kanban_quota_episode.py`
- `hermes_cli/kanban_quota_episode_release.py`
- `hermes_cli/kanban_quota_probe.py`
- `hermes_cli/kanban_quota_receipt.py`
- `hermes_cli/kanban_quota_release.py`
- `hermes_cli/turn_exit.py`

## Tests

- `tests/agent/test_quota_auth_churn.py` and `tests/hermes_cli/test_kanban_quota_auth_churn.py`: revoked-401 rows plus real 429s are not a wall; control (every row 429) stays a typed wall; 401 row with its own model cap still counts; churn receipt writes no episode; probe release clears hold and wall with audit; refused when probe confirms or cannot disprove; refuses while any held provider is walled; `--probe` flag registration and CLI exit 2.

Test files touched by these patches:

- `tests/agent/test_quota_auth_churn.py`
- `tests/hermes_cli/test_kanban_quota_auth_churn.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- 2790 alone adds only tests that fail without 2791, so apply them together. Live usage reads need an OAuth pool; unreadable usage refuses release.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.
