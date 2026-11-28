# Runtime selection binding (source correction; not fleet approval)

The launcher no longer executes `hermes --print-runtime-command`, accepts returned
flags, or substitutes a MARK inside an otherwise arbitrary executable prelude.
It reads the canonical `~/.local/bin/hermes` shell forwarder and the selected
`<core>/.hermes/bin/hermes`. Both must match the complete POSIX publisher grammar.
The inner script must equal the selected core commit's entire `_launch_template`
rendering. A small data-only AST interpreter renders it without importing core or
evaluating Python expressions. The template, recovery constants and publisher
must match their Git blobs. The Python path must match its store's recorded Python
package entry. `binding` prints paths, commit, blob IDs and SHA256 hashes only.

Normal execution uses a fixed plugin-owned isolated bootstrap and the absolute
sibling `pool_llm.py` run_path call. The authenticated inner launcher is DATA,
never the executed program: its `--run-module` / `--print-runtime-command` branches
must not interpret pool arguments. Managed `hermes_bootstrap` still owns PM
activation and recovery; this is not a frozen dependency environment. Dispatch via
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
* **Initial execution:** literal `/bin/sh` → `/usr/bin/python3 -I -B -S` parses
  selection using only OS-managed stdlib. PATH Python, cwd/user/site startup,
  PYTHONHOME and PYTHONPATH cannot supply this resolver. Symlinked pool commands
  resolve the plugin sibling in isolated Python, without PATH tools. OS shell,
  Python/stdlib and loader remain separately managed platform trust. All current
  hosts must prove that system Python exists and supports this code (>=3.9).
  This is not a claim of fleet availability until those reads arrive.
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

Before bootstrap, only pool verbs (`ask`, `models`, `check`, `serve`, `env`) and
help are admitted. Hermes-only `kanban why`, `pm repair` and post-swap update
arguments cannot enter bootstrap's special dispatch paths. This is not an
argument allowlist for provider calls: the existing pool parser still validates
each admitted verb, and the existing HTTP/token/provider policy is unchanged.

`test_managed_bootstrap.py` optionally reads an explicit `POOL_CORE_SOURCE` Git
checkout as data. It copies byte-identical bootstrap and PM environment-selection
source into disposable fixtures, exercises the actual relaunch function extracted
from its recorded source, and proves both a real reexec and executable `.pth`
activation. Repair, attestation, locks/leases, platform preferences and credential
owners are controlled adapters; both SQLite connect functions are fenced. This
is not an unmodified whole-core or installed-runtime test. Public no-core CI
skips this opt-in test; an engineering run with the explicit source is required
for this managed integration proof. Actual launcher `ask` and `models` tests use
controlled provider adapters and the real pool parser, with no provider/account
operation.

Before publication: collect installed launcher forms on every existing host,
complete managed core/dependency selection provenance and service source binding,
resolve the initial-interpreter and execution-environment edges, independently
review the final exact head, and pass required CI/canonical landing. After ordinary
sync, run `bin/hermes-pool-llm binding` on each required host. Run `check` only under
the parent's separately authorized live-provider acceptance. Never self-enroll or
infer registry/quiet-start approval from this document.
