# Update: named checkpoint refusals, duplicate gateways, deferral streak floor

Patches: 2796, 2804.

- **2796** (`9810e8988b`, fix(update): a checkpoint refusal names its cause; a duplicate `gateway run` is not a second gateway)
- **2804** (`eb15bbb72c`, Update-pause deferral detector files a repair card only once the streak outlasts a drain cool-off)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- 2796: every refusal of `checkpoint-for-update` carries a `reason` naming the check (no live intent, nonce, cron inventory, admission not held, unpublished reservations, async delegations, unpreservable work by kind with chat session keys, loop timeout). `_ask_to_drain` keeps it and the deferral prints `[cause: ...]` after the unchanged retry token "busy or cannot acknowledge an idle pause". `_discover_bare` skips a `gateway run` PID that is not its home's verified gateway (a duplicate start); unprovable identity keeps the old behavior.
- 2804: `note_deferral` filed a repair card from the third consecutive deferral by count alone, so a quick retry burst filed cards. The streak must now also span `MIN_STREAK_S = 900` seconds (first to latest deferral); unreadable `first_at` counts as old enough, and streak state accumulates unchanged.

## How it works

- `gateway/update_checkpoint.py`, `hermes_cli/update_cmd_posix_pause.py`, `hermes_cli/update_pause_deferrals.py`.

## Code

- `gateway/update_checkpoint.py`
- `hermes_cli/update_cmd_posix_pause.py`
- `hermes_cli/update_pause_deferrals.py`

## Tests

- `tests/gateway/test_update_checkpoint.py`, `tests/hermes_cli/test_update_checkpoint_pause.py` (every refusing check names itself; retry token kept; non-home gateway not paused; home/unprovable still paused); `tests/hermes_cli/test_update_pause_deferrals.py` (three retries in 41 s file nothing, floor restarts on a successful pause, unreadable first_at files); preswap discovery tests adjusted.

Test files touched by these patches:

- `tests/gateway/test_update_checkpoint.py`
- `tests/hermes_cli/test_update_checkpoint_pause.py`
- `tests/hermes_cli/test_update_pause_deferrals.py`
- `tests/hermes_cli/test_update_preswap_bare_discovery.py`
- `tests/hermes_cli/test_update_preswap_strict_discovery.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- External retry logic matches the "busy or cannot acknowledge an idle pause" text; keep that token.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.
