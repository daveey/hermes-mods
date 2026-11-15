# Portable regression fixtures and host-isolated checks

Patches: **4256, 4261, 4288, 4303, 4319, 4327–4328, 4345** (4352 snapshot).

## Behavior

Portable core-check execution refuses macOS before execution guards, while management operations and Linux execution remain available.

Test infrastructure isolates mail identity and MCP wire homes, publishes handoff verdicts atomically, and retains socket custody for deterministic collision and forwarding probes.

## How it works

The handoff test writes its generation verdict with atomic replacement. Arithmetic execution fixtures account for Bash version and terminal image-probe fixtures use real PTY replies in distinct ownership phases.

MCP wire homes disable detached installers before cleanup. Hermetic fixtures redirect mail configuration instead of falling back to a real home, without changing HOME for subprocesses. Crash-collision fixtures retain a duplicate listening socket across predecessor death and run in POSIX lanes. Closed-port probes reserve a bound non-listening socket; empty accepted probes cannot consume the HTTP response fixture.

## Code

- `evals/mcp_device_flow.py`
- `scripts/check`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/agent/test_terminal_image_vscode_probe.py`
- `tests/conftest.py`
- `tests/cron/test_lifecycle_guard_arith_lexical.py`
- `tests/evals/test_mcp_device_flow_isolation.py`
- `tests/gateway/fixtures/api_collision_daemon.py`
- `tests/gateway/test_api_crash_collision_probe.py`
- `tests/scripts/desktop_update/test_posix_handoff_pid_export.py`
- `tests/scripts/test_check_mac_admission.py`
- `tests/test_hermetic_mail_identity.py`
- `tests/tools/test_sandbox_port_forward.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

These are mostly test-only reliability changes, not new runtime sandbox guarantees. The Darwin admission policy is intentional; do not bypass it to run the portable suite on a Mac. POSIX fixture custody is not a live crash-recovery result from this pass.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
