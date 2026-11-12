# Cron: a drain deferral spends no restart rerun

Patches: 2837.

- **2837** (`c165810de9`, fix(cron): a drain deferral spends no restart rerun)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- `defer_unadmitted` re-arms a fire the draining owner never admitted via `reschedule_interrupted`, which wrote the ordinary restart_rerun marker, so the first real attempt after the deferral was taken for the rerun itself and, if cut by the next update, never rerun. Deferral markers now carry `deferred=true` and do not count; a deferred pending rerun keeps the rerun's time so the one-rerun bound holds and is re-armed instead of dropped.

## How it works

- `cron/restart_cut.py`, `cron/restart_rerun.py`.

## Code

- `cron/restart_cut.py`
- `cron/restart_rerun.py`

## Tests

- `tests/cron/test_restart_cut.py`: `test_first_run_after_a_deferral_cut_by_a_restart_is_still_rerun`, `test_rerun_after_a_deferral_cut_again_is_not_rerun`, `test_deferring_a_pending_rerun_keeps_it_a_rerun`.

Test files touched by these patches:

- `tests/cron/test_restart_cut.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Older markers without `deferred` keep the ordinary meaning.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.
