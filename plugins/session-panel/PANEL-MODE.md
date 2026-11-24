# Panel producer mode (Window2 dormant)

Do **not** enable on a real serve from the P/B/R implementation card.

## Same effective mode

- Stage selector: `HERMES_SERVE_PANEL_EXTERNAL` (`1`/`true`/`on` → external).
- Master override: `HERMES_SERVE_PRODUCERS_EXTERNAL=0` forces OFF (same drain rules).
- Environment initializes a process. An env file alone does **not** update a running serve.

## One supported runtime setter (no serve PID restart)

```
hermes producer-mode --panel-external false
```

Optional: `--installation <HERMES_HOME>` (default `$HERMES_HOME` or `~/.hermes`).

Writes `$HERMES_HOME/serve-producers/panel.mode.json` with `{external, revision}`.
OFF serves watch that revision. `true`/`false` are the only values (no second boolean).

Equivalent module dispatcher (tests): `python -m hermes_cli.serve_producer_mode --panel-external false`.

## One-flag rollback for the activation owner

1. `hermes producer-mode --panel-external false` on the installation (running OFF serves observe the new revision and drain).
2. Or set `HERMES_SERVE_PRODUCERS_EXTERNAL=0` before the next process start.

Failed drain / surviving descendant stays unavailable; restart-free does not promise instant recovery.
