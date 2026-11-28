# Panel producer mode (Window2 — dormant, fixture-proven)

Do **not** enable on a real serve from the P/B/R implementation card. External mode is selected
only by the installation-local runtime setter below or by explicit environment initialization.
Every serving process watches the same stored mode even when both variables are unset.

## Same effective mode

- Stage selector: `HERMES_SERVE_PANEL_EXTERNAL` (`1`/`true`/`yes`/`on` → external; any other value → OFF).
  A serve with this variable set (to anything) *joins the mode seam*: it initializes the installation-local
  mode, runs the legacy owner when OFF, and watches the mode file (0.5 s poll) for a new revision.
- Master override: `HERMES_SERVE_PRODUCERS_EXTERNAL=0` forces OFF. A serve started with it publishes OFF
  (a new revision if the stored mode was ON) so external peers drain.
- Unset variables never overwrite a stored mode: the runtime setter stays durable across restarts.
  A serve started with **neither** variable still joins the seam *read-only*: it creates no installation
  state and never writes the mode, runs its legacy owner, and watches the mode file (a `stat` poll; the file is
  re-read only when its stamp changes). A stored external mode is followed at start (the legacy owner is fenced
  from the first instant, so nothing starts it before the lifecycle's own transition).
- An env file alone does **not** update a running serve.

## One supported runtime setter (no serve PID restart)

```
hermes producer-mode --panel-external false     # or: true
```

Optional: `--installation <root-or-profile-home>` (default: the installation root containing the process
Hermes home). A named home such as `<root>/profiles/<name>` is canonicalized to `<root>` by both the
setter and the serving lifecycle, so it never creates a private second mode under the profile.
Equivalent module dispatcher (used by the subprocess tests):
`python -m hermes_cli.serve_producer_mode --panel-external false --installation <root-or-profile-home>`.

It writes `<installation-root>/serve-producers/panel.mode.json` = `{"external": bool, "revision": int}`
atomically under an `flock`ed lock file; the revision strictly increases. There is no second writable
boolean; the file is the same mode the environment initializes.

## Transitions (same serve PID, both directions)

OFF → EXTERNAL: strict COMPLETE admission under the held LaunchGate → positive legacy retirement (every
lane drained: feed/decision/board/watch threads, board capture workers and the capacity service; the process feed
slot cleared; the legacy fence stays up) → exec broker+child (execution fence before provider
import; provider manifest digests verified first) → completed initial publication → atomic attach of
route + event sink (READY).

EXTERNAL → OFF: withdraw the relay generation (outputs/ACKs revoked; the relay can never serve, enqueue or attach
again, so a retained callback cannot revive it) → detach (admission closed, event sink dropped) → wait
for the whole family to exit → only then start the legacy owner.

Any failed step, incomplete admission, un-retired legacy owner or surviving child/descendant leaves the panel
**UNAVAILABLE** with fencing retained: no kill, no timeout takeover, no `LOCK_UN`, no inode replacement, no
automatic fallback to legacy. A supervised serve restart after actual retirement clears it. Two OFF serves keep
the approved limitation: one fenced legacy owner, the others unavailable observers.

## One-flag rollback for the activation owner

1. `hermes producer-mode --panel-external false` on the installation. Running serves that joined the seam observe
   the new revision and drain; no serve PID restarts. Recovery after a failed drain is **not** instantaneous.
2. Also remove/flip `HERMES_SERVE_PANEL_EXTERNAL` (or set `HERMES_SERVE_PRODUCERS_EXTERNAL=0`) in the serve
   environment so the next process start does not re-select external.

## Not claimed here

No activation, no performance or freshness claim, no Desktop interaction. See the core and mods landing receipts
for the exact gates exercised.
