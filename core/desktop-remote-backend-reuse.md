# Desktop: reuse remote SSH backends across quit and reconnect

Patches **0448** (`2b3388b95d`), **0449** (`941478b69d`), **0450** (`6f9a0bc29b`).

## Behavior

Opening a session on a remote gateway cost 6-19 s because every teardown (quit, sleep-resume, tunnel rebuild)
killed the owned `serve --isolated` backend. Now a teardown reason decides the remote action: quit, bootstrap-retry,
revalidate, resume and idle-stop RELEASE the backend (tunnel and SSH close; backend and lockfile stay); deliberate
changes (fingerprint change, removed connection, recycle, config apply, profile teardown) and any unlisted reason
TERMINATE. Windows remotes are skipped. A released backend exits by itself 900 s after its last client and never
mid-turn. `connect()` logs `reuse miss: <reasons>` for every present-but-unreused backend (0450 adds the
stale-runtime-after-update case). 0449 proves ownership of macOS launcher-run backends: exact argv from `/proc`,
`sysctl KERN_PROCARGS2` or last-resort `ps`; for `python -I -c <script>` launchers the single literal
`sys.argv = [...]` is read with `ast.literal_eval` (never executed); zero/several literals or non-string items stay
foreign; the owner nonce and token-path rules still apply.

## Code

`apps/desktop/electron/ssh-teardown-policy.ts`, `remote-lifecycle.ts`, `main.ts`.

## Tests

`remote-lifecycle-release.test.ts` (release keeps backend and lock, fails closed on skew, reuse with persisted
token, exact miss reasons, stale-proof case, teardown policy table) and `remote-lifecycle-launcher-ownership.test.ts`
(real local launcher recognized; wrong nonce and two literals foreign). Not re-run for this mirror.

## Applying

Apply 0448–0450 in order.

## Gotchas

Needs the backend's idle exit (`web_server_idle_exit.py`) for the leak bound.

## Plugin?

No.
