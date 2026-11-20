# Parent-owned native CLI creation custody

Mirror entries: 4827, 4828, 4829, 4830, 4831, 4832, 4833, 4834, 4835, 4836.

## Behavior

Supported foreground local terminal launches can offer CLI card creation through the owning parent tool context, with transactional native-origin receipts rather than authority inferred from child environment strings.

## How it works

Strict admission requires current durable interactive-turn custody, revalidated in the board transaction. Receipt-backed idempotent replay and final admission are fenced against launch retirement; failed receipt checks roll back creation. A launch-private bounded packet transport uses per-request reply descriptors. The CLI maps supported options before child-side board initialization and refuses broken offers rather than falling through. Parent policy capture preserves identity, tool grants, hooks and scoped deferred dispatch; delegated children cannot borrow it. Linux foreground local integration disables yield/survival and retry after an offer. Per-spawn socket duplicates remain owned through Popen while retirement stays bounded. Common child environment creation scrubs stale locators.

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_cli_native.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_origin_admission.py`
- `hermes_cli/kanban_origin_cli_gate.py`
- `model_tools.py`
- `tools/environments/base.py`
- `tools/environments/local.py`
- `tools/kanban_tools.py`
- `tools/origin_cli_bridge.py`
- `tools/origin_cli_policy.py`
- `tools/origin_cli_terminal.py`
- `tools/terminal_tool.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Native-admission and launch-gate tests cover null receipts, revoked custody, replay, atomic rollback, unsupported side effects and post-commit unknown outcomes. Bridge/policy/deferred/CLI-create suites cover real subprocesses, sibling reply isolation, permissions, hooks, schema validation and refused fallback. Terminal/spawn-lifetime/transport-guard suites cover required Linux primitives, failure/cancellation, descriptor races, retirement, malformed/truncated packets, socket kinds, size limits and invalid replies.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/hermes_cli/test_origin_cli_create.py`
- `tests/hermes_cli/test_origin_cli_gate.py`
- `tests/hermes_cli/test_origin_native_admission.py`
- `tests/tools/test_origin_cli_bridge.py`
- `tests/tools/test_origin_cli_policy.py`
- `tests/tools/test_origin_cli_policy_deferred.py`
- `tests/tools/test_origin_cli_spawn_lifetime.py`
- `tests/tools/test_origin_cli_terminal.py`
- `tests/tools/test_origin_cli_transport_guards.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

The offer is a channel locator, not a sandbox or a grant. Legacy creation without an offer retains optional receipts but receives no trusted native custody from unsetting a locator. Already admitted writes may finish after retirement; unknown outcomes must not be automatically retried. This path is not offered to unsupported backends, background/survivable launches or delegated contexts.
