# fast-rate-limit-fallback tests

`run_tests.py` runs the plugin regressions plus core's credential-pool tests.
Use the installed Hermes runtime Python (the first element of
`hermes --print-runtime-command`), not a Python without PM dependencies:

```sh
"$RUNTIME_PYTHON" -I run_tests.py --repo "$HOME/.hermes/hermes-agent" \
  --test-site-packages "$PYTEST_SITE_PACKAGES"
python3 -m unittest discover -s harness_tests -v
```

`PYTEST_SITE_PACKAGES` is optional when the runtime already provides pytest.
If supplying it, use a directory outside the real Hermes home: core's safety
fixtures refuse reads of arbitrary state inside that home.

The chosen checkout's committed HEAD is archived into a temporary directory;
uncommitted changes and untracked files are not tested. Tests and plugin source
are staged only inside that snapshot. The plugin fixture resolves its source
relative to its own staged file, not `FAST_RATE_LIMIT_PLUGIN_SOURCE` inherited
from another run. Core's existing safety fixtures remain enabled.

Bootstrap and pytest execute in a child process. The parent owns a
`TemporaryDirectory` context, so setup exceptions, child exit codes and Python
interrupts unwind through cleanup without core's pytest guards patching the
parent. SIGKILL or machine loss cannot guarantee Python cleanup; any remaining
snapshot is temporary data, not an untracked live-checkout modification.

## Home identity and composition regression

Ownership compares resolved directory paths at registration and request time.
A symlink spelling of an enabled home is the same owner, not a foreign profile.

`plugins/account-usage/tests/test_combined.py` exercises real PluginManager
composition and complete conversations with selected and unselected accounts,
with both compatible-account rotation and endpoint-incompatible provider fallback.
`test_long_reset_rotation_and_fallback_through_symlink_home` repeats that matrix
through a real home symlink; `test_selected_and_unselected_long_reset_rotation_and_fallback`
retains the original path. Both require the final response, exact attempted
accounts/providers, cooldowns, unchanged selected-account defaults after rotation,
and no backoff call.
The backoff stub raises instead of returning a misleading terminal MagicMock.

Failure chain: discovery canonicalized the home but requests retained a symlink
alias; string-based ownership disabled eager fallback, so an endpoint-incompatible
pool entry kept core's retry gate open. The original test exposed this on hosts
whose scratch directory is a symlink, but had no portable explicit alias fixture.
Canonicalization removes the ownership mismatch; the alias matrix is the
cross-platform regression guard. Neither selection adapter code nor its peer
composition fingerprint changes.

## Verification

- Full suite against the installed core HEAD: 110 passed, including with
  `FAST_RATE_LIMIT_PLUGIN_SOURCE=/nonexistent/stale-stage/source`.
- Seven stdlib harness tests cover snapshot ownership, exclusion of untracked
  input, successful and unsuccessful child exit codes, archive/copy/spawn
  failures, injected KeyboardInterrupt/SystemExit, an actual child import
  failure, argument forwarding and stale source environment isolation.
- The original fixture fails with FileNotFoundError when the source environment
  points at a missing stage; the new fixture passes that same injected case.
- The live core's `git status --short tests/` is empty before and after the full
  run, and the temporary snapshot is removed.

`--without-plugin` is a negative control: it forwards the two routing/fallback
test files without enabling the plugin. Against the tested core it reports
27 passed and one expected failure, `test_long_reset_falls_back_when_rotation_cannot_recover`:
core retries the primary once before fallback, while the plugin-enabled test
requires immediate fallback. Both runs clean up their snapshots.
