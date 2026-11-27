# user-terminal

Runs a command in an owner-controlled terminal window, or forwards it through the
Desktop courier when the gateway has no screen. See [REMOTE.md](REMOTE.md) for the
courier protocol and [OPENER.md](OPENER.md) for local opener responsibility.

## Authorization and local data

Both local and remote tool handlers apply the default-deny `owner_refusal`
check before any job operation (including status, focus, Enter and withdrawal).
Desktop, CLI, TUI, API and local owner surfaces are accepted. The local handler
also preserves card-owned jobs for a `kanban` surface bound to
`HERMES_KANBAN_TASK` (or `HERMES_KANBAN_WORKER_TASK`); this exception never grants
remote Mac access. A card identity cannot override a messaging, cron, unknown or
mixed non-owner surface. Confirmation mode is unchanged and is not an
authorization boundary.

New job directories are created with mode 0700. Every initial job file, including
command, metadata and runner/watchdog scripts, is created exclusively with mode
0600. Scripts are invoked through their interpreter, not executed directly. Later
runtime output remains inside the private job directory. This does not repair
permissions of pre-existing jobs or redact command output.

Outbox take-back uses this plugin's own atomic rename-and-unlink implementation.
It does not import or execute `session-mentions/relay_files.py`; the wire protocol
and courier claim-versus-cancel behavior are unchanged.

## Tests

From the repository root, run the complete suite and repository guards:

```sh
scripts/test-all -k user-terminal
# Public CI parity, without Hermes core:
scripts/test-all --no-core -k user-terminal
```

Use an isolated HOME/HERMES_HOME for fixtures; `--hermes-core PATH` selects an
installed core explicitly. Linux skips tests requiring the macOS `script` syntax.
`tests/test_source_review_boundaries.py` covers default-deny local operations,
accepted owner surfaces, private modes under multiple umasks and a hostile sibling
module that must never execute during take-back.
