# macOS opener responsibility

`open_window` checks the OS-reported responsible process, not the parent PID or
whether Python itself lives inside an app. When `~/Applications/Hermes Python.app`
is installed and the caller is attributed elsewhere (for example an SSH server),
the existing job's opener runs as a one-shot GUI launchd job using that app.
`HERMES_USER_TERMINAL_OPENER_APP` can select another locally installed Python app.
This does not install an app, request a grant, move the backend, or change job ownership.
Without that app, the original direct terminal fallback is unchanged.

The helper verifies its own responsibility before sending Apple Events. Direct
signed-app callers (gateway/TUI and workers) retain the original route. Per-job
`opener-caller.json` records caller attribution; relays also write
`opener-responsibility.json`, `opener.plist`, `opener.log`, and an atomic
`opener-result.json` when the helper finishes. The parent can return earlier when
the runner has actually started, then removes its exact launchd label. A failed
launch/helper remains a failure with attribution; it never silently retries under
the untrusted caller. All routes share the existing runner start gate.

## Why this failed

1. SSH-backed chats timed out opening terminal windows: the original AppleScript
   child inherited SSH responsibility.
2. Running a signed interpreter was insufficient: macOS responsibility follows the
   launching context, not just the interpreter path.
3. The opener selected terminals by availability without checking this context.
4. Existing tests mocked AppleScript failures but did not vary caller responsibility.

The routing guard fixes that gap; the launchd helper provides the separate
responsibility domain without a persistent service or a new TCC grant.

## Tests

Run from the repository root:

```
uv run --with pytest python -m pytest -q -p no:cacheprovider plugins/user-terminal/tests/test_opener_responsibility.py
```

The routing suite covers desktop-SSH, gateway/TUI, and worker caller attribution;
relay failure without direct fallback; single-shot launch configuration and
scrubbed identity; exact-label cleanup on success and bootstrap failure; and a
helper refusing incorrect responsibility. The direct-terminal suite remains
`plugins/user-terminal/tests_user_terminal.py` and isolates app discovery so mocked
subprocesses cannot accidentally submit launchd jobs on the developer's machine.

Live acceptance additionally requires an actual SSH-attributed caller to return
`ran/0` for `echo ok`, its helper's responsibility record to identify the installed
app, and a worker call to succeed. A disk update alone does not reload already
imported plugin modules: use normal plugin activation/reload, not backend restarts.
