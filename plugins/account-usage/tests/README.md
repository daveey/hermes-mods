# Account-usage tests

Use a Python environment containing pytest and the core dependencies. Tests do not
install dependencies or launch a new runtime. Core source resolution uses
`HERMES_TEST_CORE`, then core already importable on `PYTHONPATH`, then
`~/.hermes/hermes-agent`. An invalid explicit path fails with an ImportError and
setup guidance; it does not fall back or skip the compatibility check.

From the repository root (set `CORE` and `PYTHON` to absolute paths):

```sh
timeout 300 env -i HOME=$(mktemp -d) TMPDIR=$(mktemp -d) PATH=/usr/bin:/bin \
  PYTHONPATH="$CORE" HERMES_TEST_CORE="$CORE" \
  "$PYTHON" -m pytest -p no:xdist -q plugins/account-usage/tests
```

Keep temporary directories under your scratch root and remove them after inspection.
`conftest.py` places import-time application state in a temporary test-owned
`HERMES_HOME`. The source locator sets the existing
`HERMES_DISABLE_LAZY_INSTALLS=1` guard because core entry-point modules can import
bootstrap transitively. Direct core imports do not need the application launcher.
The child-process registry test also imports core directly.

Verified on Linux with core `20478baa674`: all 13 modules, **264 passed and 70
subtests passed** (previous coverage: 262 and 67). The added subprocess cases cover
explicit path, PYTHONPATH, home fallback, and readable missing-core failure.
The scratch HOME contained no files and no runtime was provisioned. An AnyIO pytest
deprecation warning and existing relay teardown diagnostics are separate from
isolation. Compatibility pins remain unchanged: newer unaudited core revisions
must still fail the live-core check, not skip it.

The browser harness now imports the repository's desktop plugin and puts screenshots
under `HERMES_TEST_ARTIFACTS` or the system temporary directory. Those path edits
were syntax-checked; the browser flow is not part of the Python acceptance run.
