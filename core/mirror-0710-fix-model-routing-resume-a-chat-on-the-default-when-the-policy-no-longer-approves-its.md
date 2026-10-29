# fix(model-routing): resume a chat on the default when the policy no longer approves its stored model [daveey]

Patch 0710, source `81c2bebb123fd5b235db653d6587935775bb1cd6`.

## Behavior

fix(model-routing): resume a chat on the default when the policy no longer approves its stored model [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `"""Restore persisted routing intent without granting new routing permissions."""`
- `def restore_route(model, config):`
- `class APIServerAdapter(OpenAICompatRoutesMixin, BasePlatformAdapter):`
- `def stored_session_route(session_meta, *, current_model, current_provider):`
- `def test_incomplete_exact_tuple_fails_closed(provider):`
- `+"""A resumed chat whose stored model is not an approved subscription route resumes on the profile default.`
- `import pytest`
- `async def test_background_deferred_route_sends_native(managed, runner, native_tr`
- `routing_home = fixtures.routing_home`
- `def test_desktop_saved_paid_override_never_sends(managed, monkeypatch, tmp_path)`
- `def probe_class_runtime(model, provider, config):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> With model_routing.enforce_subscription_only on, every resume adapter replayed the session row's stored
> concrete model/provider as a pin. A chat saved on a retired or unregistered model (claude-opus-4-6) or on a
> paid route (openrouter/...) then failed closed at agent build on every attempt and could never be reopened:
> hermes-hub, 10-28 00:07 PDT, session 20261027_085427_ac4078, "agent build failed ... Managed subscription
> policy rejects unregistered or ambiguous concrete pin".
>
> Why: the stored tuple is history, not a new request, yet Desktop (tui_gateway restore_class_overrides), CLI
> --resume and one-shot (stored_session_route), the API server (_stored_session_model) and persisted exact pins
> (restore_route, used by the API and messaging gateways) all re-requested it as an explicit pin. Reproduced live
> on hermes-hub (core 69c52e9402) with a probe session stored on claude-opus-4-6: the Desktop build and
> `hermes -z --resume` both refuse with the error above; `hermes chat --resume` announces "Model restored from
> session: claude-opus-4-6" and is then served claude-opus-5-5.
> Not caught: the enforcement rollout probed only fresh sessions; nothing resumed a pre-enforcement chat.
>
> Fix: agent.model_routing_restore.stale_managed_pin() asks the same pin_policy() the runtime enforces. Each
> adapter drops a tuple the policy would refuse, so the session runs on the configured default (logged at INFO).
> Approved pins still restore exactly, nothing changes with the policy off, and an explicit request for the
> stale model still refuses.
>
> Changed expectations: three tests from 40f638ec5b/20ede5adee asserted that a restored paid pin fails closed:
> test_restored_pin_is_not_managed_authorization, the gateway deferred-runtime test (was
> test_restored_paid_pin_still_hits_billing_guard) and the Desktop builder test (was
> test_desktop_saved_paid_override_never_sends). Each now resumes on the configured default and keeps its safety
> checks: an explicit request for the paid tuple still refuses before any allocation, the Desktop and gateway
> builds run the default with an empty fallback chain, the builder test sends nothing, and the gateway test
> captures only native subscription sends. A persisted exact pin is written only after pin_policy approved it
> (model_routing_managed.py resolve_managed_runtime; all 127 on hermes-hub at 10-28 01:45 PDT are subscription
> tuples), so one goes stale only when the registry later drops its route, which used to make every such chat
> un-resumable.
>
> Tests: tests/agent/test_resume_stale_managed_pin.py, one per entry path; 6 fail with the wiring reverted.

## Code

- `agent/model_routing_restore.py`
- `gateway/platforms/api_server.py`
- `hermes_cli/cli_model_switch_mixin.py`
- `tui_gateway/class_routing.py`

## Tests

Source test files changed by this commit:

- `tests/agent/test_model_routing_restore.py`
- `tests/agent/test_resume_stale_managed_pin.py`
- `tests/gateway/test_deferred_managed_runtime.py`
- `tests/tui_gateway/test_managed_entry.py`

New or changed test definitions present in the source diff:

- `test_only_an_unapproved_stored_tuple_is_stale_under_enforcement`
- `test_nothing_is_stale_with_the_policy_off`
- `test_desktop_resume_of_a_stale_model_follows_the_profile`
- `test_desktop_resume_is_unchanged_with_the_policy_off`
- `test_cli_resume_of_a_stale_model_keeps_the_launch_default`
- `test_oneshot_resume_of_a_stale_model_keeps_the_ambient_choice`
- `test_api_session_resume_of_a_stale_model_follows_the_default`
- `test_an_explicit_request_for_a_stale_model_still_refuses`
- `test_a_persisted_exact_pin_the_policy_no_longer_approves_is_not_restored`
- `test_messaging_gateway_resume_drops_a_stale_exact_pin`
- `test_restored_paid_pin_resumes_on_the_default_route`
- `test_desktop_saved_paid_override_resumes_on_the_default`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
