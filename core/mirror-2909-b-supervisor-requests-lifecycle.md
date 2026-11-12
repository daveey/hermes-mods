# Supervisor requests: survive requester completion, expire at 24 h

Patches: 2800, 2833.

- **2800** (`01788a16c4`, fix(kanban): completing a card no longer voids its filed supervisor request)
- **2833** (`99148d3cd6`, fix(kanban): expire supervisor requests pending > DEADLINE_S at accept)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- 2800: `requester_refusal` rejected a done requester's request, losing operator comments a worker files as its last act. It now refuses only a missing or archived requester (archiving stays the cancel signal). The operator call forwards `requested_at` = the request's submit time so the home can prove the caller's run was live (±120 s). Tool text says completing a card does not cancel a filed request. `retry_limit` follows the same rule.
- 2833: with no executor online a request could run days later. The executor now rejects at accept any request no executor took within `DEADLINE_S` (24 h): the ordinary rejected receipt (event, comment, wake for a parked requester, error `expired: pending > DEADLINE_S`, every op `not_applied`). Only `attempts == 0` rows expire; `_expire` uses compare-and-swap and keeps attempts at 0 so a crashed expirer retries.

## How it works

- `hermes_cli/kanban_supervisor_requests.py`, `kanban_operator_call.py`, `kanban_retry_limit_exec.py`, `tools/kanban_tools_schemas.py`.

## Code

- `hermes_cli/kanban_operator_call.py`
- `hermes_cli/kanban_retry_limit_exec.py`
- `hermes_cli/kanban_supervisor_requests.py`
- `tools/kanban_tools_schemas.py`

## Tests

- `tests/hermes_cli/test_kanban_operator_call.py` (comment lands after requester completes; late forward proven at submit time), `test_kanban_retry_limit_exec.py` (archived requester still rejected), `test_kanban_supervisor_requests.py` (finished/archived requester, unlink after completion, expiry at accept, just-under-deadline runs, old accepted request redelivered, crashed expiry repeats, CAS loser).

Test files touched by these patches:

- `tests/hermes_cli/test_kanban_operator_call.py`
- `tests/hermes_cli/test_kanban_retry_limit_exec.py`
- `tests/hermes_cli/test_kanban_supervisor_requests.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- An archived requester still voids its requests at execution; the 24 h cap matches the operator-call forward cap.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.
