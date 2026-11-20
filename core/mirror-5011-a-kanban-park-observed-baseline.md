# Kanban park: generated card probes baseline at park time

Mirror snapshot 5011; ordinals 4371, 4390.

Entries:

- **4371**: fix(kanban): baseline generated card probes at park time
- **4390**: test(kanban): pin generated probe command provenance

## Behavior

- A first park on a producer already in a wait-check default status (blocked/review) woke immediately because only earlier `condition_met` events counted as handled. Local confirmation now records an observation, and probes that the system generated (marked `auto_card_probe`) use the latest park observation as their implicit baseline. Explicit commands, `--until` and `--from` keep prior behavior; a newer `condition_met` keeps precedence; departure is persisted inside the generation-fenced result transaction so blocked -> running -> blocked wakes again. The 24-hour backstop is unchanged.
- 4390 pins provenance: scheduled and continued event command mismatches are rejected with matching controls, and CLI JSON awaiting reuse preserves `auto_card_probe` when an explicit replacement wake check is supplied.

## How it works

- `kanban_wait_baseline.py` and `kanban_wait_check.py` carry the observation; `kanban_awaiting.py` marks generated probes (inferred card records are left unmarked); `kanban_wake_check.py` applies the baseline.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_awaiting.py`
- `hermes_cli/kanban_wait_baseline.py`
- `hermes_cli/kanban_wait_check.py`
- `hermes_cli/kanban_wake_check.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/hermes_cli/test_kanban_park_observed_baseline.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Legacy parks carry no provenance bit, so identical explicit and generated commands cannot be told apart retroactively and keep prior behavior.
