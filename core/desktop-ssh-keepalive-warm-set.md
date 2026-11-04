# Desktop: SSH keep-alive bounded to the warm set

Patches: **1844** (`e27818e466`, `fix(desktop): bound the SSH keep-alive to the warm set so one-off dials stop pinning remote backends [daveey]`), **1845** (`53ea9e1a68`, `fix(desktop): keep-alive bound — re-hold on use at once, test resume skip and pending-reconnect release [daveey]`), **1846** (`9f9efb6f93`, `test(desktop): pin the warm-set wiring where it now lives (remote-warm-set.ts) [daveey]`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- Desktop main holds the idle-exit-blocking `/api/ws` keep-alive only for SSH-isolated backends in the warm set, so a one-off dial (for example a sweep over a host's profiles) no longer pins every such backend for the whole Desktop session (1844).
- Use re-holds a released backend at once, a release cancels a pending reconnect, and the post-resume rebuild retires dead unheld scopes instead of redialling them (1845); the warm-set wiring test follows its new module (1846).

## How it works

`apps/desktop/electron/ssh-keepalive-hold.ts` (`SshKeepaliveHolds`) tracks holds; `remote-warm-set.ts` defines the warm set; `main.ts` wires them.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1844: Desktop main held an idle-exit-blocking /api/ws keep-alive (#106935) on every published SSH-isolated scope until teardown, so a single sweep over a host's profiles kept every one of those backends alive for the whole Desktop session (9 serve --isolated on the mini, 4 outside the prewarm set, no renderer client, holding the BB memory gate at WARN).
- 1845: Review follow-ups: noteUse re-holds immediately (no 15 s gap before a released backend's idle grace could end), the post-resume rebuild filter lives in SshKeepaliveHolds.rebuildIfHeld with its own log line and tests (dead unheld scope retired, not redialled; bare/local/non-SSH/default/prewarm/recent redialled), a release cancels a pending reconnect (mutation-checked), and the warm-set test cleans its temp dir.
- 1846: test(desktop): pin the warm-set wiring where it now lives (remote-warm-set.ts) [daveey]

## Code

`apps/desktop/electron/main.ts`, `apps/desktop/electron/remote-warm-set.ts`, `apps/desktop/electron/ssh-isolated-keepalive.ts`, `apps/desktop/electron/ssh-keepalive-hold.ts`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `apps/desktop/electron/remote-warm-dispatch.test.ts`
- `apps/desktop/electron/ssh-keepalive-hold.test.ts`: `releases only scopes outside primary/default/prewarm/recent use, without disarming them`, `a released socket is never redialled until the scope is used again`, `holds a used scope for ACTIVE_USE_HOLD_MS, then lets it go`, `a profile entering the prewarm set is held again`, `never releases bare, local or non-SSH scopes (resume rebuild unchanged for them)`, `start/stop drive a periodic reconcile`, `holds exactly the persisted recent profiles + default; a touch joins the set and persists`, `HERMES_DESKTOP_REMOTE_PREWARM=0 empties the warm set but keeps default held`, `a release cancels a pending reconnect; noteUse and a republish re-hold at once`, `post-resume: retires a dead unheld scope without redialling it; held scopes are redialled`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
