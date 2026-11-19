# Remote CI monitor deadlines

Read and explicitly set a remote-execution wrapper's independent command timeout before launching a CI monitor. Its deadline must exceed the wait's `--timeout-min` plus one polling interval and request/cleanup slack. Shortening the inner wait alone does not change the outer deadline.

Run the supported `wait_for_ci.py --once` with the same head/repository/branch first. Exit 2 is pending, not a failed test. Start a still-needed monitor with `terminal(background=true, notify=true, persist_on_release=true)`; never hold the chat in a foreground wait or poll its background process for completion. Bound foreground remote probes well below the terminal cap.

Verify the actual remote process and execution parameters, not just the local wrapper's start. If replacing a misbounded monitor, stop only that exact owned monitor, using identity-safe signalling, and verify its exit before starting another. Leave GitHub CI running.

A wrapper timeout is not a CI verdict: recover the real state with `--once`. Do not increase a workflow's acceptance deadline to repair an observation process. After a cancelled monitor, treat its delayed failure notification as superseded only when the replacement or completed CI has been independently verified.
