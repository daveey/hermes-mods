# Read-only compute-rentals providers

## Integration

Load this package once and retain the returned bound methods:

```python
from providers import build_collectors  # when plugin directory is on sys.path
collectors = build_collectors()
for provider, collect in collectors.items():
    inventory.register_adapter(provider, collect)
```

For isolated disk-plugin loading without modifying sys.path:

```python
package_name = '_compute_rentals_providers'
package_path = plugin_dir / 'providers'
spec = importlib.util.spec_from_file_location(
    package_name, package_path / '__init__.py',
    submodule_search_locations=[str(package_path)],
)
module = importlib.util.module_from_spec(spec)
sys.modules[package_name] = module
spec.loader.exec_module(module)
collectors = module.build_collectors()
```

Each callable returns `{provider,machines,status,error,scope,observed_at}`.
`state_detail` is JSON **text** (compatible with core's string allowlist),
`rate_note` is explanatory text. Missing region is explicit `unknown`; missing
name falls back to provider ID. Stable key/ownership remain core responsibilities.
No raw provider response is exposed or logged. Preserve `partial`, `error`, and
`unconfigured` in the UI; missing access is not a successful zero inventory.

EC2 is cached for 300 seconds with a lock, four concurrent regional workers,
explicit NextToken pagination, SDK-standard retries via AWS CLI, and bounded
command timeouts. **Do not rebuild collectors for every UI poll.** Keep the
core's normal provider/inventory poll cache for the other services too.

## Scope and credentials

- Vast: `VAST_API_KEY` process environment (including broker injection), otherwise
  existing `~/.config/vastai/vast_api_key`. Resolves account ID from current user;
  requests only inventory-safe `select_cols`, paces instance pages at 1.1 seconds,
  checks unique-ID count against declared total, excludes deleted contracts.
  Current stopped-storage rate differs from advertised active GPU+disk rate.
- SF marketplace: `SF_API_TOKEN` or `SF_API_KEY` process environment. Missing
  token reports **unconfigured**. Real adapter implements `/v2/instances`
  starting_after pagination and cached `/v2/skus/{id}` enrichment. It
  deliberately does not invent per-instance allocation prices, pool-derived
  expiry, or SSH routes.
- EC2: the profiles in `ec2.profiles` (default `$AWS_PROFILE` or `default`),
  account identity via STS, every currently enabled region from
  describe-regions. Profiles not listed are not scanned. Excludes
  terminated/shutting-down instances. Public on-demand pricing uses exact
  OS/tenancy filters, spot uses exact AZ/type/OS and never falls back to
  on-demand. Unknown rates stay null. Compute estimates exclude
  storage/network/IP/credits/discounts/tax. No guessed SSH AMI user.
- RunPod / Modal / Prime Intellect: API keys are read only inside a worker
  subprocess (`python -m providers.<id> --worker`), optionally wrapped by
  `broker.command` so a secrets manager injects the key. Missing key reports
  **unconfigured**/**error**, never an empty success.
- Slurm: fixed read-only `scontrol`/`squeue` commands over SSH
  (`BatchMode=yes`, `StrictHostKeyChecking=yes`) to the hosts in `slurm.hosts`
  only; bounded by time and bytes. No hosts configured = **unconfigured**.

Extra providers (see `__init__.py`) follow the same contract and are loaded from
`extra_providers_dir`.

All provider calls are reads. No node execution, provisioning, stop/delete,
invite acceptance, billing setting change, or plugin enablement is implemented.

## Verification

`pytest plugins/compute-rentals/tests -q` (from the hermes-mods root)

Tests cover request shapes, pagination, repeats, counts/deduplication, status
conflicts, auth refresh, rate conversion and unknowns, exact OS/AZ pricing,
per-region/workspace partial failure, timeout/error redaction, retry-after,
credential-safe allowlisting, cache behavior, and normalization defaults.
