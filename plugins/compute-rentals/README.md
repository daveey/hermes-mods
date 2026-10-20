# compute-rentals

A read-only inventory of the GPU and compute you're renting, across clouds, in one Desktop panel.
It also keeps a local registry that records which Hermes session started which box, so idle boxes
can be traced back to a session. It never creates, stops or changes anything in a cloud.

Desktop half: [desktop-plugins/compute-rentals](../../desktop-plugins/compute-rentals/).

## Providers

| id | What it reads | Credentials |
|---|---|---|
| `vast` | Vast.ai instances | `VAST_API_KEY`, else `~/.config/vastai/vast_api_key` |
| `runpod` | RunPod pods | `RUNPOD_API_KEY` (or vended by `broker.command`) |
| `lambda` | Lambda Cloud on-demand instances (USD list price from the instance type; `ssh ubuntu@<ip>`) | `LAMBDA_API_KEY` (or vended by `broker.command`; worker module `providers/lambdacloud.py`) |
| `modal` | Modal apps, tasks and sandboxes | `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET` (or vended by `broker.command`); needs the `modal` package |
| `primeintellect` | Prime Intellect pods | `PRIME_API_KEY` (or vended by `broker.command`) |
| `sfcompute` | SF Compute marketplace instances | `SF_API_TOKEN` / `SF_API_KEY`; reports `unconfigured` without one |
| `ec2` | EC2 instances in every region, per AWS profile | the AWS CLI and your profiles |
| `slurm` | Slurm nodes and jobs on hosts you list, over ssh | your ssh keys; reports `unconfigured` with no hosts |

A provider you have no credentials for reports `unconfigured` or `error`, never an empty "ok".

## Configuration

Everything is optional. With no config file you get the defaults below. The file is
`~/.hermes/compute-rentals.yaml` (`$HERMES_HOME/compute-rentals.yaml`); set
`COMPUTE_RENTALS_CONFIG` to use another path.

```yaml
# Extra providers (see below). Default: ~/.hermes/compute-rentals/providers
extra_providers_dir: ~/.hermes/compute-rentals/providers

ec2:
  profiles: [default]        # AWS CLI profiles to scan. Default: [$AWS_PROFILE or "default"]
  cache_seconds: 300         # EC2 results are cached this long

slurm:
  hosts: "gpu[0-3]"          # list, or Slurm hostlist syntax. Empty = provider unconfigured
  user: ops                  # ssh user (default: your ssh config)
  control_host: gpu0         # where scontrol/squeue run; must be one of hosts

broker:
  # Optional argv prefix that runs the runpod/lambda/modal/primeintellect worker with its API key
  # injected, e.g. a secrets manager. "{provider}" is replaced with the provider id.
  command: [op, run, --env-file, "~/.config/{provider}.env", --]
  python: null               # interpreter for that worker (default: .extra-runtime/bin/python, else Hermes's)
```

Unknown top-level keys are kept, so an extra provider can read its own section through
`providers.config.load_config()`. An invalid file raises an error naming the bad key.

## Extra (private) providers

Every `*.py` in `extra_providers_dir` is loaded as a submodule of this plugin's `providers` package.
Files starting with `_` or `test_` are skipped. A module defines:

```python
from .common import result          # relative imports resolve inside the plugin's providers package

PROVIDER = 'my_cluster'              # [a-z0-9][a-z0-9_-]*, must not clash with a built-in
LABEL = 'My cluster'                 # optional display name

def build_collector():
    def collect():
        return result(PROVIDER, [...], scope="...", errors=[])
    return collect
```

`collect()` returns `{provider, machines, status, error, scope, observed_at}`, the same as the
built-ins. A module that fails to import or build is logged and shows up as that provider with
status `error`; the other providers keep working. This is how I keep providers for internal
clusters out of this public repo.

## Python dependencies

`modal` (for the Modal provider) isn't in the Hermes runtime. The config file is parsed with PyYAML
or, failing that, ruamel.yaml (which Hermes ships); with neither, it must be JSON. The broker
worker runs from `.extra-runtime/` in this folder if it exists:

```bash
cd ~/.hermes/plugins/compute-rentals
uv venv .extra-runtime --python 3.12 && uv pip install --python .extra-runtime/bin/python modal httpx pyyaml
```

## Tests

```bash
cd ~/.hermes/plugins/compute-rentals
uv run --with pytest --with pyyaml --with fastapi --with httpx \
  python -m pytest -q -p no:cacheprovider tests                                # backend
node --test tests/plugin.test.mjs   # Desktop panel; needs Node >= 22 (jsdom uses require(esm))
```
