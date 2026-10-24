# Proposed implementation map and serialized ownership

Only `plugins/runners/contracts/` is authored in this subtask. Everything below is a
**future implementation boundary**, not a list of files shipped or modified here.
Relative paths avoid personal deployment details. Private configuration, inventory,
credentials, evidence and screenshots must not be copied into public examples.

| Boundary | Proposed files | Owning units / responsibility |
|---|---|---|
| Contract freeze | `plugins/runners/contracts/*` | A: schemas, fixtures, API/tools, identity, source-baseline caveats. |
| Registry/domain | `plugins/runners/{models,config,store,health}.py` | B: normalized config, foreign-key/uniqueness semantics, WAL migrations, pure health and retention. |
| Read collection | `plugins/runners/{collector,service}.py`, `providers/{base,github_actions}.py`, `probes/{ssh,macos,linux}.py` | C: complete pagination, source provenance, fixed probes, singleton/fencing. |
| Tool/API registration | `plugins/runners/__init__.py`, `dashboard/plugin_api.py`, `plugin.yaml`, `dashboard/manifest.json` | Shared serialized hot paths; no plugin import/registration is supplied by A. |
| Native Desktop | `desktop-plugins/runners/plugin.js`, `tests/{plugin,transport,repair,keyboard}.test.mjs`, test SDK harness | D, then E2, then I; owner-aware SDK transport and native chrome, no new web app. |
| Repair session | `plugins/runners/repairs.py` and tests | E/E2: durable reservation and authorized start intent; core owns sessions/turns. |
| Safe operations | `plugins/runners/{operations,credentials}.py`, `broker/{server,authorization}.py` | F: separate broker identity/credentials/grants, host locking and tested quiescence. |
| GitHub dispatch | `plugins/runners/jobs.py`, `broker/claims.py` and tests | G: immutable source, single-use OIDC claim, exact job/artifact provenance. |
| Routing | `plugins/runners/execution.py`, `tests/test_execution.py` | I: recipes, eligibility, deterministic preflight, tool/terminal semantics. |
| BB catalog adapter | `plugins/runners/providers/babybear.py`, `tests/test_babybear.py` | J: existing bridge/catalog, named commands/receipts, no direct pod authority. |
| Workspace wire/executor | shared negotiated protocol, BB hub adapter and dedicated worker; exact BB files require current source mapping | K/K3: new bounded capability, no repurposed long-running `script.run`. BB changes use its own reviewed release pipeline. |
| Supervision | `plugins/runners/scripts/{run_collector,install_service}.py`, `templates/{collector.service,collector.plist,broker.service,runners.example.yaml}` | Explicit opt-in; no installation-on-import or active gateway restart. |
| Public docs/install | `plugins/runners/README.md`, `skills/devops/runners/SKILL.md`, existing root `README.md` and `install.sh` | Later documentation/rollout owner; no edits in this subtask. Preserve concurrent root README work. |
| Backend test suites | `plugins/runners/tests/test_{config,store,health,github,probes,collector,api,tools,repairs,operations,jobs,security}.py` | Respective implementation units, injected clocks/transports and fake credentials. |
| Core durable-start seam | `tui_gateway/session_start_requests.py`, `tui_gateway/{methods_session.py,contracts/sessions.py}`, `tests/tui_gateway/test_session_start_requests.py` | Separate core worktree. New `session.start_request`; no changes in live checkout. |
| Generated core contracts | `apps/shared/src/gateway-contract.generated.ts`, `apps/shared/src/gateway-contract.openrpc.json` | Generate through existing core contract generator after typed seam exists. |
| Core SDK/preflight | Existing owner-aware SDK transport/routing and narrow recipe preflight integration | Extend existing seams only if topology tests require it; no parallel courier. Mirror core changes with public feature docs through normal packaging. |
| Private deployment | Owner-profile registry configuration, service enablement, scoped reference resolution and private operations docs | Main inventory/deployment owner; runtime DB/logs/locks/evidence never synchronized into public git. |

## Serialization contract

The shared implementation hot files are:

* `plugins/runners/__init__.py`
* `plugins/runners/dashboard/plugin_api.py`
* `plugins/runners/collector.py`
* `plugins/runners/broker/server.py`
* `desktop-plugins/runners/plugin.js`

Their shared-rule edit sequence is **B → C → E2 → F → G → J → I → K3**.
Desktop-specific files follow **D → E2 → I** as well; honor both dependencies for
shared `plugin.js`. Contracts A establishes vocabulary only and does not preempt any
implementation owner's file. Rebase on the latest main before editing; preserve prior
behavior and unrelated dirty files. Core SDK docs and root public README also have
concurrent owners: integrate rather than overwrite. No commit/push/board transition is
part of this contract-only handoff.

## Acceptance ownership

A's other work (live inventory parity, private verified mappings, actual permission
results, native baseline screenshots) is owned separately and is not inferred from these
fixtures. Runtime gates stay with B–L: health/collector, exact owner session startup,
guarded management, live Linux/macOS canaries, BB read/action readback, workspace jobs,
agent routing and UI evidence. A structural schema pass does not complete any of them.
