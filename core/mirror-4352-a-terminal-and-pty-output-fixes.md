# PTY output drain after child exit and noclobber-safe environment dumps

Mirror snapshot 4352; ordinals 4186, 4242, 4243.

Entries:

- **4186**: fix(process): drain a PTY child's buffered output after it exits
- **4242**: terminal: keep exports when the session sets noclobber
- **4243**: test: atomic snapshot write expects the noclobber-safe >/ redirect

## Behavior

- **PTY drain** (4186): the PTY reader ran `while pty.isalive()`; a reader starved until after the child exited left the loop without reading and published an exited session with empty output. After the loop (POSIX) the reader now drains whatever the master holds through the shared selector with a zero timeout, capped at `_FINAL_DRAIN_MAX_CHARS`, so a descendant that keeps the slave open cannot hold up completion.
- **noclobber** (4242, 4243): the environment re-dump and login bootstrap wrote `export -p` with plain `>` into the file `mktemp` created; a session with `noclobber` (`set -C`) refused it, the `mv` was skipped and exports were lost between commands. They now use `>|`. 4243 updates `TestAtomicSnapshotWrite` to expect `>|` and still forbids in-place writes.

## How it works

- `tools/process_registry.py` plus new `tools/process_registry_pty_drain.py` (chunk handling shared between loop and drain); `tools/environments/base_session_env.py`.

## Code

Shipped non-test paths (union over these entries):

- `tools/environments/base_session_env.py`
- `tools/process_registry.py`
- `tools/process_registry_pty_drain.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/tools/test_base_environment.py`
- `tests/tools/test_local_persistent_shell.py`
- `tests/tools/test_process_registry_pty_drain.py`
- `tests/tools/test_sudo_askpass_delivery.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- A shell older than bash 4 (macOS system bash) is covered by the noclobber change; the test hardens against reaching a real host `sudo`.

## Plugin?

None.
