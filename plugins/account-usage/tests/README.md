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

Verified on Linux with core `a229655398887f15a65ab2a838f243611462a65d`:
**267 passed and 70 subtests passed**, with no relay shutdown diagnostics. The
peer fallback runner passed **104 tests** against the same core. The prior isolated
core `20478baa674` run passed 264 tests and 70 subtests. Subprocess cases cover
explicit path, PYTHONPATH, home fallback, and readable missing-core failure.

## Compatibility audit

### Core through `311d8339309` / `21acd343ff6` (2026-11-01)

- `agent/credential_pool.py` (`311d8339309`): a pushed no-refresh OAuth token keeps a
  live Codex-shaped quota mark instead of clearing it, and the refresh path returns
  `None` for such a row. `choose_snapshot` reads persisted rows and already rejects an
  exhausted row via `_exhausted_until`, so a manual choice now (correctly) refuses an
  account a pushed token would previously have un-benched. No wrapped seam changed.
- `agent/turn_api_error.py` (`d76f44fcd9d`, `21acd343ff6`): `handle_api_error` handles
  `SubscriptionQuotaUnavailable` (class fallback or terminal return) before
  credential recovery. It is raised only by class model routing (`model_routing_runtime`),
  which `managed_routing_refusal` already refuses for a manual choice; the branch never
  calls `recover_with_credential_pool`, and a stale `_account_usage_failed_attempt` is
  cleared at the next middleware boundary. fast-rate-limit-fallback's
  `handle_api_error` AST pin was appended for the same diff: the new branch returns
  before `route_classified_error`, so its long-reset bypass is unaffected.
- `check_compat.py` now also checks fast-rate-limit-fallback's `compatibility.json`
  function pins (stdlib AST digest, tested equal to that plugin's live fingerprint):
  the watcher had reported only the account-usage pins while the combined tests
  failed on the fast-fallback pin.
- Hub run note: with `HOME` a temp dir, the `anthropic` PM extra is not found; append
  the installed extra's `site-packages` to `PYTHONPATH`. Result on core `1c96fe663f1`
  and an `origin/main` export: **278 passed, 70 subtests**; fast-rate-limit-fallback
  `run_tests.py`: **104 passed**.

### Core `a22965539888`

The full diff from `20478baa674` to `a22965539888` changes two pinned files:

- `agent/chat_completion_helpers.py`: classify `StreamLifetimeExceeded` with
  transport timeouts via `_as_lifetime_error`, and mark an unwound attempt released
  before close/backoff. The wrapped interruptible entry points, credential
  factories, context propagation and client ownership boundaries are unchanged.
- `agent/turn_recovery.py`: forward `api_error` to `exhausted_copy` for the terminal
  error headline. Recovery routing and credential selection are unchanged.

Only these two new byte digests were appended to `execution-compatibility.json`;
older audited hashes remain valid. No runtime selection code or peer manifest
changed. `test_pin_lists.py` retains unknown/malformed/missing-source rejection,
while `test_combined.py` exercises real plugin discovery, both load/unload orders,
long-reset rotation/fallback and home alias composition. The compatibility guard
correctly caught this drift: a newer core is not implicitly trusted.

## Relay teardown regression

Observed with nemo-relay 0.10.3: pytest reported success, then native session close
logged `scope handle not found` and `root scope cannot be removed`.

- Why did scope identity break? Turn fixtures used `patch.dict(sys.modules, ...)`
  to substitute one gateway module. Exiting it restores the **entire** module map,
  removing dependencies lazily imported by the real turn while retained Relay
  objects still reference them. Pre-importing Relay prevented the failure; a
  standalone real Relay session/turn/shutdown also closed cleanly.
- Why was that rollback used? The fixture treated the module cache as an ordinary
  fixture dictionary rather than process-lifetime dependency identity. All such
  server substitutions now use `module_override`, restoring only the target key.
- Why was it not caught? The errors appeared after pytest's success summary, at
  process exit. The new cold-subprocess regression reads stdout/stderr after exit
  and fails on those diagnostics, without suppressing logging or exceptions.

Restoring the old substitution makes the new regression fail (1 failed, 2 passed);
with the fix all three new cases pass. They also verify target restoration after
an exception, absent-key cleanup and preservation of unrelated late imports.
The complete suite has no teardown diagnostics. No core/dependency change or
warning filter was needed.

The browser harness now imports the repository's desktop plugin and puts screenshots
under `HERMES_TEST_ARTIFACTS` or the system temporary directory. Those path edits
were syntax-checked; the browser flow is not part of the Python acceptance run.
