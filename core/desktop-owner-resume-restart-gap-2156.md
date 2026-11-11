# Owner-routed opens during a gateway restart gap

Patch **2137**.

## Behavior

An explicit owner-routed Desktop open can resume its session while the previously active gateway is closed. Opens without an explicit owner still wait for the active gateway. Background settings views do not dispatch chat resumes.

Runtime discovery can observe a returning private control owner after `service_identity_unverified`, without executing an unverified service definition or spawning an unmanaged replacement.

## How it works

`canDispatchRouteResume` requires the chat view and either an open gateway or a fresh owner-bearing request whose session matches the routed session. Request sequences prevent redispatch while the old gateway remains unavailable. Existing session actions connect to the owner and publish its history.

`_recover_start_refusal` observes only the unverified-service case. `_await_unverified_service_owner` polls through absent/starting states, bounded by the caller deadline and a thirty-second cap. Other verdicts return immediately; update fences still block attach. If no owner returns, the result remains inaccessible with the unverified-identity reason.

## Code

- `apps/desktop/src/app/session/hooks/use-route-resume.ts`: route/owner/sequence dispatch gate.
- `hermes_cli/gateway_runtime.py`: bounded observation of returning control owners.

## Tests

Source tests inspected; **not executed in this mirror pass**:

- `apps/desktop/src/app/session/hooks/use-route-resume-owner.test.tsx`: `dispatches an explicit remote open while the previous local gateway remains closed`; `still waits for the active gateway when the requested session has no explicit owner`; `does not resume from a background settings view even with an explicit owner`.
- `apps/desktop/src/app/session/hooks/use-route-resume-owner-composed.test.tsx`: `opens the remote owner and publishes its history while the local gateway stays closed`; `does not use an owner request for a different session to bypass the closed gateway`.
- `tests/hermes_cli/test_gateway_runtime_restart_gap.py`: `test_restart_gap_reverifies_returning_control_owner`, `test_permanent_service_refusal_preserves_reason_and_deadline`, `test_observation_caps_long_caller_budget_at_thirty_seconds`, `test_returning_owner_refusal_is_not_readiness`, `test_update_fence_arriving_during_observation_blocks_attach`, `test_other_start_refusals_are_not_observed_or_delayed`, and `test_observation_keeps_waiting_through_starting_until_owner_verdict`.

Composed Desktop tests use mocked transport, not a live restart.

## Applying

Use the [core README full applying recipe](README.md#applying), including its pinned replay plan and integration bridges. This patch is not standalone.

## Gotchas

Observation does not authorize launching an unknown service. Identity, profile, protocol and update-fence refusals are not readiness. An owner request for a different routed session does not bypass the gate; an ignored request is not consumed prematurely.

## Could this be a plugin?

No supported plugin boundary owns both built-in route-resume dispatch and gateway startup discovery. These safety conditions belong in core.
