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

`--without-plugin` forwards the two routing/fallback test files without enabling
the plugin. Its argument forwarding is unit-tested; its behavioral suite is not
part of the enabled-plugin acceptance run above.
