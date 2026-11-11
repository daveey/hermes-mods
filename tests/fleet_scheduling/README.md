# Fleet scheduling regressions

Explicit, bounded Linux integration tests against a selected Hermes core checkout.
These are fixture-fleet proofs, not production outage observations. No background
service is installed and no active backend is restarted.

## Run

Use the selected core's managed **test** Python (pytest and core dependencies).
The host needs passwordless sudo, `unshare`, `ip`, `mount`, `setpriv`, `timeout`,
and delegated cgroup v2 memory control for the actual OOM variant.

```sh
nice -n 10 "$TEST_PYTHON" scripts/run-fleet-scheduling \
  --core-source "$CORE_SOURCE" --sync-source "$PRIVATE_STATE_SOURCE" \
  --output "$PRIVATE_EVIDENCE_DIR" --scenario all --timeout 240

FLEET_CORE_SOURCE="$CORE_SOURCE" nice -n 10 "$TEST_PYTHON" -m pytest -q \
  -p no:cacheprovider --confcutdir=tests/fleet_scheduling \
  tests/fleet_scheduling/test_runner.py
```

The private state tree must supply `tests/test_fleet_update_resume.py` with
`run_update_resume(fleet_root, containment, board_factory, monkeypatch)`.
Missing it fails the update scenario; it is never silently skipped. To run
individual scenarios select `provider`, `crash`, `wake`, `update`, or `remote`;
`isolation` runs just containment controls. Do not run scenario pytest files
outside the launcher. Host-side `test_runner.py` is separate and must also pass.
Its OOM-unavailable integration test requires an explicit `FLEET_CORE_SOURCE`.

## Proof boundaries

- Provider: loopback HTTP 429, nonzero cooldown, neutral retry, review handoff,
  final completion, and implementation mutations for retry, premature dispatch,
  and fabricated human blockers.
- Crash: separate executor SIGKILL and actual bounded cgroup OOM, kernel
  `memory.events` evidence, original-work recovery. The executor joins its own
  cgroup; no asynchronous numeric PID is migrated. Missing OOM capability gives
  `UNVERIFIED` and a nonzero aggregate exit, never a substituted SIGKILL pass.
- Wake: real workers park, repair broken probes, re-park and complete. Exit 1
  remains not-ready; exit 2/timeouts are broken, not condition-met. A legitimate
  approval stays gated.
- Update: private adapter runs cooperative pause and persisted real continuation
  across two disposable runtime artifacts. Fixture update transport is not a
  production deployment or an installed core-version upgrade.
- Remote: real collect/sweep paths across fresh processes, delayed completion,
  failures/crashes around persistence and acknowledgement, transcript-based
  durable dedupe, and silent history.

Kernel loopback-only network and hidden production DBus endpoints supplement
fixture path, PID-start/pidfd, credential and inherited-descendant fences.
Registration exclusion uses disposable sentinel stores/transports, not live
production database queries. The launcher retains per-scenario preflight,
source/module provenance, assertions, transitions, bounded waits, raw output,
exit status and cleanup in JSON v1 receipts. Cleanup failure is failure and
preserves the fixture for diagnosis. Store receipts privately: they contain
absolute paths and runtime diagnostics.

Run the same commands from published/installed sources after shipping. Pass
`--sync-runtime /absolute/path/to/installed/hermes-sync` to import the installed
updater itself; its bytes must match the selected state source, and the adapter
records its resolved path and digest (a mismatch is refused).
Keep
source-level testing, publication, installed-source verification and production
observations separate. A PASS on a worktree does not establish deployment.
