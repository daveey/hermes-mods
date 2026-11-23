# Always-on rollover: replace a running-but-unresponsive backend (mirror 5438–5443)

## Behavior
- A running always-on backend that stops answering `/api/health` is reported by `drift()` as "does not answer /api/health" instead of "no drift".
- `supervise()` rolls such a backend only after `UNRESPONSIVE_CONFIRMATIONS` (4) consecutive polls, so a seconds-long stall never triggers a roll. Confirmations are counted per reason kind: commit-drift polls never complete an unresponsive count, and the reverse.
- A roll leaves the old backend running so it retires through its idle fence; live turns are not cut.
- A backend rolled away from for not answering is recorded in `demoted.json`. `pick_port` never re-adopts it, and `reap_superseded` stops it after `DEMOTED_HARD_S` (30 min, SIGTERM checkpoints turns) instead of the 6 h superseded backstop, so starved backends cannot fill `BACKEND_PORTS`.
- `demote_if_replaced` demotes only a port that was actually replaced; if the starved backend exits and its replacement starts on the freed port, the now-active port is not demoted.
- `Roller.start()` (the first roll, run on every core update) also demotes an active backend that is running but not answering. It waits for the update marker first, then demotes only if all `UNRESPONSIVE_CONFIRMATIONS` probes, `START_PROBE_GAP_S` apart, miss.
- A stale (timed-out) update marker holds the start-up roll once (`UPDATE_WAIT_S`), not twice: `roll()` takes `marker_waited`, and `start()` passes True only for a stale marker, not one that cleared.

## How it works
`hermes_cli/always_on_rollover.py` extends `drift()`, `supervise()`, `pick_port`, `reap_superseded`, `Roller.start()` and `Roller.roll()`. Two hard-stop backstops now exist (6 h superseded, 30 min demoted) plus the unresponsive roll.

## Code
- `hermes_cli/always_on_rollover.py`

## Tests
Source coverage in `tests/hermes_cli/test_always_on_rollover.py` (stubs the real-port probe so live backends on the host cannot affect `pick_port`): unresponsive drift, confirmation counting and reset via a scripted fail/ok/fail health sequence, demotion kept just under `DEMOTED_HARD_S`, failed roll demotes nothing, freed-port race, start-up demotion for starved vs healthy backends, no probe while the marker is present, one wait for a never-clearing marker, `marker_waited=False` after a clear wait. Not executed in this mirror pass.

## Applying
Apply the six patches in order to `hermes_cli/always_on_rollover.py` and its test file. No config change.

## Gotchas
- Detection time depends on the supervise poll cadence (`SUPERVISE_POLL_S`, 15 s) times the 4 confirmations; start-up probing is 4 `/api/health` probes `START_PROBE_GAP_S` (3 s) apart plus each probe's own timeout.
- Demoted backends are SIGTERMed after 30 min whether or not they passed their idle fence.

## plugin?
No. Core CLI change.
