# Test discovery isolation and infrastructure stability

Patches: 1596 1599 1604 1621

- `patches/1596-test-active_sessions-late-contender-skips-.N.tmp-sta.patch`
- `patches/1599-fix-tests-refuse-every-unittest-discovery-form-load_.patch`
- `patches/1604-test-write-pause-boundary-child-handshake-files-atom.patch`
- `patches/1621-daveey-test-pm-retry-dropped-PyPI-connections-in-loc.patch`

Related: [config-backups-live-home-guard.md](config-backups-live-home-guard.md) describes the earlier package-level unittest refusal and live-home isolation guard extended by 1599.

## Behavior

- **1596 (active-session contention test):** the late contender reads only published result files, ignoring dot-prefixed `.N.tmp` staging files. The parent stops waiting when any contender exits unsuccessfully, kills remaining workers, and reports the failed workers' stderr rather than waiting for the cohort deadline. The contention test exercises the normal path; deliberate contender-crash injection is untested in this patch.
- **1599 (unittest discovery refusal):** test packages refuse discovery through `load_tests`, and top-level test modules reject imports outside the `tests.` namespace. Added subprocess tests cover discovery from the repository with `-s tests` and no `-t`, discovery from inside `tests`, and bare unittest invocation from either directory. Each verifies refusal and preservation of a disposable sentinel config. Tree-wide structural tests enforce both guard patterns; they do not execute every possible discovery command.
- **1604 (pause-boundary handshake):** the child publishes its ready PID and consumed-marker JSON through atomic replacement, so existence-based waiters do not read a newly created but still-empty destination. This changes the test harness, not production pause-record writes; no dedicated forced partial-write test is added.
- **1621 (locked wheelhouse fixture):** a dropped TLS connection wrapped in `URLError` is retried, successfully downloaded bytes are hash-checked and cached by URL, and a missing wheel's HTTP 404 fails without retry. Broader transient-error handling and retry exhaustion are implemented but not separately tested here.

## How it works

**1596:** `_CONTENDER_SCRIPT` adds `results()`, filtering entries whose names start with `.`. Both the late worker's search for an `OK` holder and the holder's completed-result count use this helper. The readiness wait also ends if a worker exits. A second wait polls for all workers to exit or any nonzero exit; the parent records failures, kills live workers, drains their pipes, and asserts with the failing stderr values.

**1599:** each test-package `__init__.py` receives a `load_tests(loader, tests, pattern)` hook raising `RuntimeError` with `Use scripts/run_tests.sh; unittest discovery bypasses test isolation`. Top-level `tests/test*.py` modules receive an early import guard raising `ImportError` with the same message when `__name__` does not start with `tests.`. Where necessary, the guard follows `from __future__` imports. Package hooks cover discovery that imports packages under short names; module guards cover top-level discovery that bypasses the root `tests` package. The patch changes 144 files using these repeated patterns plus the safety regression tests, rather than adding a separate implementation to each module.

**1604:** the `cell` fixture's child script defines `put(path, text)`: write UTF-8 text to the sibling `<name>.tmp`, then `os.replace` it onto the destination. The consumer uses it for both handshake outputs before the parent reads them.

**1621:** `_download_wheel` defaults to five attempts with a 60-second `urlopen` timeout and exponential backoff starting at one second. It retries `URLError`, `http.client.HTTPException`, `ConnectionError`, `TimeoutError`, and `ssl.SSLError`. `HTTPError` is handled first: status codes below 500 raise immediately; higher codes retry until the final attempt. A completed download must match the expected `sha256:` digest before entering `_DOWNLOADED`. `locked_wheelhouse` writes these returned bytes into the fixture wheelhouse. The module-level cache can be reused by modules importing the same fixture in one process.

## Code

- **1596:** `tests/hermes_cli/test_active_sessions.py` — contender subprocess script and parent coordination.
- **1599:** test-package `__init__.py` files throughout `tests/` (for example `tests/hermes_cli/__init__.py` and `tests/pm/__init__.py`), top-level `tests/test*.py` modules, and regression additions in `tests/test_unittest_safety.py`. This is a pattern summary of the 144 changed files, not a full inventory.
- **1604:** `tests/hermes_cli/test_update_pause_boundaries.py` — `cell` fixture and consumer handshake writes.
- **1621:** `tests/pm/test_runtime_wheelhouse.py` — `_download_wheel`, `_DOWNLOADED`, `_TRANSIENT`, `locked_wheelhouse`, and download regressions.

## Tests

- **1596:** `tests/hermes_cli/test_active_sessions.py::test_cross_process_acquire_claims_only_one_last_slot` is the existing cross-process test whose coordination is repaired; no new fault-injection test is added.
- **1599:** `tests/test_unittest_safety.py` adds:
  - `test_uncovered_discovery_forms_are_refused` with `root-no-t`, `inside-tests`, and `inside-tests-bare` cases.
  - `test_bare_unittest_at_repo_root_is_refused`.
  - `test_every_test_package_refuses_discovery` — scans package initializers for the hook signature and refusal message.
  - `test_every_top_level_test_module_has_import_guard` — scans top-level test modules for the guard marker and refusal message.
  The subprocess helper accepts a working directory and gives each run a disposable home containing a sentinel config.
- **1604:** `tests/hermes_cli/test_update_pause_boundaries.py::test_consumption_checkpoints_debt_before_unlink_and_producer_death` uses the revised child handshake in the existing `claim_first`-parameterized flow.
- **1621:** `tests/pm/test_runtime_wheelhouse.py::test_wheel_download_retries_a_dropped_tls_connection` injects one wrapped TLS EOF, then successful bytes, and confirms a second helper call uses the cache; `test_wheel_download_does_not_retry_a_missing_wheel` asserts that HTTP 404 causes exactly one request.

These were not rerun by the mirror pass.

Untested:

- **1596:** deterministic staging-file rename interleavings, deliberate early/nonzero contender exits, and a measured fail-fast latency bound.
- **1599:** exhaustive execution of every package/start-directory/pattern combination, direct-file and custom-loader entry paths, and pytest compatibility specifically as a new regression in this patch. Structural scans check text, not execution ordering or semantic equivalence.
- **1604:** forced scheduling between temporary-file creation and replacement, and interruption or filesystem failure during publication.
- **1621:** HTTP 5xx recovery, non-404 HTTP failures, each other transient exception class, exhaustion of all attempts, exact backoff timing, hash mismatch, actual remote PyPI recovery, and reuse across separate test modules. The cache-reuse test covers two calls in one module.

## Applying

Apply these patches in published series order: 1596, 1599, 1604, then 1621, with their preceding test infrastructure already present. In particular, 1599 extends the earlier unittest isolation guards, 1604 assumes the pause-boundary fixture, and 1621 assumes the locked-wheelhouse fixture. Standalone application of this subset is not certified. No runtime configuration is added; use `scripts/run_tests.sh` for the core suite rather than unittest discovery.

## Gotchas

- These are test-suite changes, not changes to active-session admission, production pause handling, or the production package downloader.
- Discovery refusal is intentional even with a disposable `HERMES_HOME`; the package hooks and namespace guards are not conditional on whether the home is live.
- A new test package or top-level module needs the corresponding guard to satisfy the structural safety tests.
- Atomic handshake publication uses replacement, not an explicit durability/fsync protocol. The fixed temporary name assumes one writer per handshake destination.
- Wheel downloads still require network access before offline staging. Retries do not repair missing wheels or digest mismatches.
- `_DOWNLOADED` is in-memory and keyed only by URL. A cache hit returns already verified bytes without checking a newly supplied expected digest, so callers must keep a consistent locked hash for a given URL. It is not a persistent or cross-process cache.

## Plugin?

No. These patches modify core test discovery, subprocess coordination, and test-fixture downloads, which are not plugin extension points.
