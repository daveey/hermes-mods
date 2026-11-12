# Plugin activation reaches every chat-hosting serve backend

Patches: **2492**.

## Behavior

Plugin activation (`hermes plugins enable`, and the mods sync's backend activation) used to reach only the backends that held a host role record. Two Desktop-owned backends can run at once (a supervised always-on SSH backend plus a local Desktop pool child, or two pool children); both claim `desktop-serve`, the loser publishes nothing, and if the winner exits only the lock file is left. The surviving backend then kept its old plugin hooks. Now every serve backend also publishes its own per-process entry, so activation notifies it too, whatever the role-lock outcome.

## How it works

- New `gateway/host_backends.py`: `publish()` writes `serve-backends/<pid>.json` (a `HostRecord`) and a 0600 `<pid>.token` under the host state dir (directory mode 0700). A backend with no port or no token publishes nothing; only the `serve` and `desktop-serve` roles are allowed.
- `read_entries()` returns live entries oldest-process first, skips entries whose file name is not their pid or whose protocol version differs, and prunes provably dead ones (start ticks + boot epoch no longer match). An unprovable one is kept for the token check and the dial to settle.
- `token_is_consistent()` requires the entry token to still hash to the record's fingerprint (no torn restart). `token_for()` returns the entry's own token, else the role token only when the role record names the same pid; never another process's token.
- `host_rendezvous.register_exit_cleanup()` removes the entry on exit (atexit and SIGTERM), sharing the signal-handler install with `cleanup_on_exit`.
- `web_server._publish_host_rendezvous` calls `host_backends.publish(...)` before claiming the role lock.
- `plugins_activation.serve_backend_records()` lists role records first, then token-consistent entries not already listed (deduped by pid/port); `notify_serve_backend_record` dials with `host_backends.token_for(record)`.
- Discovery only: the attach/refuse ladder still reads role records; an entry never makes a backend a host owner. See [plugin-activation-serve-fanout.md](plugin-activation-serve-fanout.md) for the role-record fan-out this extends.

## Code

`gateway/host_backends.py` (new), `gateway/host_rendezvous.py`, `hermes_cli/plugins_activation.py`, `hermes_cli/web_server.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/gateway/test_host_backends.py` (entry publish/read/prune/clear and token rules against a real rendezvous dir and real processes) and `tests/hermes_cli/test_plugin_activation_second_desktop_backend.py` (a real second process that only publishes an entry is activated beside a role-holding pool child; survivor found after the role holder exits; torn token skipped; entry removed on exit; `web_server._publish_host_rendezvous` publishes an entry when the role lock is held by another). Covered entry path: `plugins_activation.notify_serve_backend(s)` and `serve_backend_records()`. The `hermes plugins enable` CLI and the mods-sync caller are untested here; Windows file modes are not asserted.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Entries are published only when the backend has a dialable port and a session token.
- A backend started before this change has no entry; activation still reaches it only through its role record.
- Stale entries are pruned lazily by the next reader.

## Plugin

Core change; no plugin required.
