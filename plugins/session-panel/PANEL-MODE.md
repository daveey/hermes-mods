# Panel producer mode (Window2 — dormant, fixture-proven)

Do **not** enable on a real serve from the P/B/R implementation card. Nothing here is selected
unless a serve is launched with one of the two environment variables below.

## Same effective mode

- Stage selector: `HERMES_SERVE_PANEL_EXTERNAL` (`1`/`true`/`yes`/`on` → external; any other value → OFF).
  A serve with this variable set (to anything) *joins the mode seam*: it initializes the installation-local
  mode, runs the legacy owner when OFF, and watches the mode file (0.5 s poll) for a new revision.
- Master override: `HERMES_SERVE_PRODUCERS_EXTERNAL=0` forces OFF. A serve started with it publishes OFF
  (a new revision if the stored mode was ON) so external peers drain.
- Unset variables never overwrite a stored mode: the runtime setter stays durable across restarts.
  A serve started with **neither** variable does not touch the seam at all (legacy behaviour unchanged).
- An env file alone does **not** update a running serve.

## One supported runtime setter (no serve PID restart)

```
hermes producer-mode --panel-external false     # or: true
```

Optional: `--installation <HERMES_HOME>` (default `$HERMES_HOME` or `~/.hermes`).
Equivalent module dispatcher (used by the subprocess tests):
`python -m hermes_cli.serve_producer_mode --panel-external false --installation <HOME>`.

It writes `<HERMES_HOME>/serve-producers/panel.mode.json` = `{"external": bool, "revision": int}`
atomically under an `flock`ed lock file; the revision strictly increases. There is no second writable
boolean; the file is the same mode the environment initializes.

## Transitions (same serve PID, both directions)

OFF → EXTERNAL: strict COMPLETE admission under the held LaunchGate → positive legacy retirement (feed
threads joined and the process feed slot cleared) → exec broker+child (execution fence before provider
import; provider manifest digests verified first) → completed initial publication → atomic attach of
route + event sink (READY).

EXTERNAL → OFF: revoke the output/ACK generation → detach (admission closed, event sink dropped) → wait
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
