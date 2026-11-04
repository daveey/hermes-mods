# Gateway thread profiler: bounded cost, 25 Hz default

Patches: **1712** (`81d892a3ba`, `[daveey] Bound active gateway profiler cost and test CPU isolation`); **1728** (`9df8181e8d`, `[daveey] Lower gateway profiler default to 25 Hz after live 50 Hz overrun`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- An active `hermes gateway profile` run bounds its own hot-path cost, with a CPU-isolation test (1712).
- The default sampling rate is 25 Hz (`DEFAULT_HZ` in `gateway/control_profile.py`) after live 50 Hz runs missed the <3%-of-a-core acceptance on two of three runs (1728).

## How it works

Extends [gateway-thread-profiler.md](gateway-thread-profiler.md); `scripts/benchmark_gateway_profile.py` measures the profiler thread's share of one core.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1712: (subject only)
- 1728: The post-deploy no-override live check at the 50 Hz default measured the gateway-profiler thread at 1.3854%, 3.6331% and 3.3729% of one core (123/166/149 threads), missing the <3% acceptance on two of three runs. Explicit 25 Hz live probes measured 1.7418%, 1.8144% and 2.1481% (147/152/171 threads).

## Code

`gateway/control_profile.py`, `hermes_cli/subcommands/gateway.py`, `scripts/benchmark_gateway_profile.py`, `website/docs/developer-guide/gateway-profiling.md`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/gateway/test_control_profile.py`: `test_control_verb_omitted_hz_uses_default_and_explicit_hz_overrides`
- `tests/gateway/test_control_profile_active_hotpath.py`: `test_active_neighbour_keeps_stable_histogram_run_open`, `test_active_runs_flush_before_thread_identity_replacement`, `test_changed_leaf_entering_suspendable_chain_disables_reuse`, `test_greenlet_appearing_during_active_runs_disables_reuse`
- `tests/gateway/test_control_profile_cpu_isolation.py`: `test_sleeping_thread_does_not_inherit_burner_cpu`
- `tests/hermes_cli/test_gateway_profile.py`: `test_cli_and_control_defaults_agree`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- The live CPU percentages quoted in 1728's message are measurements on the source host, not reproduced here.

## Plugin?

No: these change core modules that plugins cannot hook.
