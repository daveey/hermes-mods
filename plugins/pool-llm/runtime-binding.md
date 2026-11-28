# Runtime selection binding (draft; not fleet-approved)

The launcher no longer executes `hermes --print-runtime-command`, accepts returned
flags, or substitutes a MARK inside an otherwise arbitrary executable prelude.
It reads the canonical `~/.local/bin/hermes` shell forwarder and the selected
`<core>/.hermes/bin/hermes`. Both must match the complete POSIX publisher grammar.
The inner script must equal the selected core commit's entire `_launch_template`
rendering. A small data-only AST interpreter renders it without importing core or
evaluating Python expressions. The template, recovery constants and publisher
must match their Git blobs. The Python path must match its store's recorded Python
package entry. `binding` prints paths, commit, blob IDs and SHA256 hashes only.

Normal execution uses that validated script, replacing only its exact terminal
`hermes_cli.main` tail with the absolute sibling `pool_llm.py` run_path call.
It preserves the managed bootstrap and actual SystemExit status. Dispatch via
`cProfile` is NOT used: that module catches SystemExit, hiding a token refusal's
nonzero exit status. Routine core commits and republished Python paths require no
private per-host pool manifest. Unsupported launcher grammars refuse; support for
all currently deployed hosts must be proven before landing this draft.

## Separate trust domains — provenance is not approval

* **Custom code:** launcher, runtime_binding.py and sibling pool_llm.py are in the
  plugin review. PATH-selected discovery scripts and arbitrary returned prelude
  are removed, not allowlisted. Auth/token/browser/rebinding rules are unchanged.
* **Managed code:** hermes_bootstrap; hermes_cli._early_recovery; PM selection,
  repair and environments; agent.model_routing/model_routing_managed;
  agent.credential_pool; agent.anthropic_adapter; agent.auxiliary_client;
  hermes_cli.auth_codex/config; runtime_provider and secret_scope; process bootstrap
  and their transitive imports require a separate exact selected core map/review.
  Three publication source blobs are NOT that map or approval of the entire core.
* **Managed dependencies:** interpreter/stdlib/native loader, selected dependency
  environment, anthropic/openai/httpx and transitive SDK imports require actual PM
  selection/lock/artifact evidence. A hash of Python is identity, not authenticity.
  Same-uid mutation and an update racing resolution remain managed-host custody.
* **Initial execution:** the helper's `/usr/bin/env python3` shebang still depends
  on the invoking environment. This remains an explicit unresolved full-authority
  edge in this draft, not implicitly approved by removing discovery.
* **Environment:** runtime/bootstrap overrides and secret-source configuration can
  affect managed execution. Offline `binding` does not run them or prove their
  closure. Do not credit the read-only resolver as normal execution containment.
* **Service:** plugin.yaml's service name is not an installed unit. Unit fragments,
  drop-ins, ExecStart, EnvironmentFile selection and actual manager state require
  independent installed reads; a future committed template cannot establish parity.
* **Provider endpoints:** no live provider/account/credential operation is part of
  these tests or `binding`. `check` DOES call the providers; it is not read-only
  binding inspection. Do not run it under a no-model-call restriction.

## Verification and remaining gates

`test_runtime_binding.py` covers malicious PATH discovery/prelude nonexecution,
complete launcher refusal, data-only rendering, unrecorded interpreter refusal,
ambient resolver override nonselection, routine core + Python publication updates,
and real env exec. The full plugin cohort preserves mocked ask/models/provider
routing, token serialization, real loopback auth/SDK/streaming tests. These are
fixture claims, not installed fleet binding or full executable-closure approval.

Before publication: collect installed launcher forms on every existing host,
complete managed core/dependency selection provenance and service source binding,
resolve the initial-interpreter and execution-environment edges, independently
review the final exact head, and pass required CI/canonical landing. After ordinary
sync, run `bin/hermes-pool-llm binding` on each required host. Run `check` only under
the parent's separately authorized live-provider acceptance. Never self-enroll or
infer registry/quiet-start approval from this document.
