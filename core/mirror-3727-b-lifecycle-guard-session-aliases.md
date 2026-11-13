# Lifecycle guard: benign session aliases no longer make call arguments executed

Patches: **3722–3724**.

## Behavior
- The terminal lifecycle guard no longer refuses a command just because the shell session holds some alias (for example a stock `alias ll='ls -l'`) when a script merely mentions a live database path as a call argument, such as `os.path.expanduser("~/.hermes/kanban.db")` inside a heredoc run through `bash -ls` over ssh.
- A heredoc body that only reads an over-cap spill file is likewise not refused, locally or inside an ssh body.
- Still fail-closed: a `(` whose segment contains an actual session alias word stays in the executed view, and when alias names are unknown (`None`) every call argument is still treated as executed.

## How it works
- 3722 replaces `_LifecycleScanBudget.aliases_possible` with `alias_names`: the known session alias names, or None when any name may be an alias (unknown snapshot, non-str names, or walked text that may define an alias; sticky). `_iter_segment_contexts` takes `alias_names` and keeps a `(` in the executed view only if its opener segment holds a known alias word (any word, covering blank-ending alias chains). `_walk_candidates` treats a call-argument candidate as executed only when alias names are unknown.
- 3723 is test-only: adds a case for a heredoc body reading an over-cap data file.
- 3724 moves `_may_define_alias` and `_session_aliases_shadow_consumers` unchanged into the new `cron/lifecycle_guard_aliases.py` to keep `lifecycle_guard.py` under its line-count target.

## Code
- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_aliases.py`
- `tests/cron/test_lifecycle_guard_session_aliases.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_lifecycle_guard_session_aliases.py`: `test_card_repro_is_not_refused_under_benign_session_aliases`, `test_session_led_call_still_fails_closed`, `test_alias_led_session_subshell_runs_in_real_bash`, `test_local_backend_benign_snapshot_alias_allows_card_repro`, `test_heredoc_body_reading_an_over_cap_data_file_is_not_refused`.
- 3724 (module move) adds no tests of its own.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Alias effects are assumed to stay within the segment naming the alias; an alias whose value leaves a quote or heredoc unbalanced is not modeled (as elsewhere in the guard).
