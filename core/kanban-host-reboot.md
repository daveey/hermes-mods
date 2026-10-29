# Book a host reboot as `host_reboot`; block the card that ran the shutdown

Patch 0958 (`43cb700f69`, full `43cb700f697aad1aa2ed693bad637eca417ad016`).

A worker whose subprocess runs a real shutdown used to be booked `crashed` ("pid N not alive") with every other card that died in the same reboot. The sweep counted a failure on each and retried the card that had run the shutdown, which rebooted the host again. Patch 0958 tells that death from a crash, requeues bystanders without a failure, and blocks the card the previous boot's sudo journal attributes the power command to.

## Behavior

- The dead-worker sweep still reclaims only this host's `running` cards whose worker pid is gone and whose launch grace has elapsed. Other hosts' claims are untouched.
- A death is a host reboot only when both boot ids are known and differ. The run outcome and the board event are `host_reboot`, not `crashed`.
- A bystander (reboot, but this card did not match the journal) goes back to the run's source phase with `consecutive_failures` unchanged.
- A culprit (this card matched an executed sudo power command) is force-tripped to `triage` with `block_kind` `capability`, quoting the journal line, and is not retried.
- Same-boot deaths, and any death where either boot id is unknown, stay ordinary crashes. The journal is not read.

## How it works

Spawn already stores `worker_started_at` as `_process_fingerprint`: `"<boot_id>:<pid1 start>|<start>"` (macOS may append `@<boottime>`). `boot_of` returns the text before the first `|` and before the first `:` inside that prefix. `current_boot` reads `/proc/sys/kernel/random/boot_id` and returns `""` on `OSError`.

`_reclaim_dead_workers` loads every `running` row with a worker pid, counts reboot victims, then classifies each local dead worker as before and calls `note`. `note` no-ops unless `rebooted_since` is true. Otherwise it sets `_DeadWorker.host_reboot`, sets `event_kind` to `host_reboot`, and adds `host_reboot`, `spawn_boot`, and `current_boot` to the event payload. `run_outcome` checks `host_reboot` first, so the closed run is `host_reboot` even when the classifier had said `crashed`, `signaled`, `oom_killed`, or `rate_limited`. If an `on_kanban_worker_exited` subscriber is registered, the hook payload keeps the classifier's `exit_kind` and sets `outcome` to `host_reboot`.

The reclaim transaction releases the claim and writes the run and the `host_reboot` event for every such card. Accounting then splits:

- Bystander (`host_reboot` and no `reboot_culprit`): stamp `last_failure_error` (500-character cap), append the id to `sweep.host_rebooted`, do not call `_record_task_failure`.
- Culprit: falls through into `crash_details`. `_account_crashes` force-trips it (`block_kind` `capability`, `sticky` on the `gave_up` event) and comments.

`detect_crashed_workers` still returns only `sweep.crashed`. Bystander ids are not in that list. They are stored on `detect_crashed_workers._last_host_rebooted`. `_run_reclaim_phase` copies `_last_auto_blocked` and `_last_rate_limited` onto `DispatchResult` and does not copy `_last_host_rebooted`. Culprit ids are in the crashed return value and, when the trip lands, in `auto_blocked`.

### Known boot id requirement

`rebooted_since` is true only when all of these hold:

1. The fingerprint contains `|`. An int, `None`, empty, or the literal `unverified` yields no spawn boot. A failed fingerprint capture is stored as `unverified`, not NULL, and is not a reboot.
2. The boot-id half (before `|`, then before `:`) is non-empty. The instantiation epoch is `"<boot_id>:<pid1 start>"`, or `""` when neither `/proc` file is readable. A container recreate changes pid 1's start and leaves `boot_id` alone; this check compares `boot_id` only, so a same-kernel container restart is not a host reboot.
3. The current boot id is non-empty. `current_boot` does not guess. `victims` reads it once and passes that string through the sweep, so one unreadable `/proc` treats the whole tick as "no reboot".
4. The two strings differ. They are compared as stored (the dashed id from `/proc`). They are not normalized before the compare. Stripping dashes happens only later, for `journalctl -b`.

If any condition fails, the worker stays on the normal crash path: outcome `crashed`, a failure is counted, and `power_commands` is not called. A same-boot death is the tested form of this rule.

### Journal attribution limits

Attribution reads only the spawn boot's sudo journal, and only to name a culprit. It never decides that a reboot happened.

`power_commands` strips dashes, lowercases, and requires `^[0-9a-f]{32}$`. Anything else returns `[]` without calling `journalctl`. A valid id is read with `journalctl -b <id> _COMM=sudo --no-pager -q -o short-iso` (10s timeout, stdin discarded). A zero exit is parsed and cached for the process, including an empty journal. A nonzero exit is parsed as empty text and cached the same way, so a permission or "no such boot" failure looks like "no power command" for the rest of the process. `OSError`, `subprocess.SubprocessError` (including timeout), and `ValueError` return `[]` and are not cached, so the next card retries. A custom reader is not cached.

`parse_power_commands` keeps a line only when all of these hold:

- It matches `sudo[<digits>]: <user> : <fields>`.
- The line does not match `command not allowed`, `incorrect password`, `a password is required`, or `NOT in sudoers`. A refused shutdown is not a culprit.
- Fields split on ` ; ` include a `COMMAND` that matches a power command: path-optional `shutdown`, `reboot`, `poweroff`, or `halt`; path-optional `systemctl` with optional arguments then `reboot`, `poweroff`, `halt`, or `kexec` as its own token; or path-optional `init` / `telinit` with `0` or `6`.

Not attributed, so every rebooted card is a bystander: a console, API, or cloud reboot; a power command that did not go through sudo; `systemctl restart <unit>`; `systemctl status reboot.target`; a refused sudo attempt; an unreadable or non-32-hex boot id. The stored line is capped at 400 characters. An allowed `shutdown -k` still matches the power regex (it does not check flags), so a wall-only shutdown can be treated as the reboot command.

`find_culprit` walks those commands in journal order and returns the first hit for this card. Cards are matched independently; the code does not pick a single global culprit. Match order for each command:

| Match | Rule |
|---|---|
| `workspace` | `PWD` is `workspace_path` or a descendant. `normpath` plus a `/` boundary. No symlink or realpath resolution. Skipped when `workspace_path` is empty. |
| `task_id` | The card id is a full path component of `PWD`, not a substring of another component. |
| `profile_scratch` | This assignee has exactly one reboot victim, and `PWD` is under that profile's scratch dir. |

Scratch dirs, rooted at Hermes home (walked up from `.../profiles/<name>` when this process is already inside a profile):

- assignee `default`: `<hermes home>/cache/scratch`
- any other assignee: `<hermes home>/profiles/<assignee>/cache/scratch`
- no assignee: no scratch dir, so this match cannot fire

A reboot victim is a host-local `running` card whose spawn boot differs from the current one. The count is taken before the grace and liveness filters. Two victims of the same assignee disable scratch matching for both, even if one of them is still inside launch grace and is not reclaimed this tick. Workspace and `task_id` matches do not require a sole victim. No match means bystander, including when the journal names some other card's workspace.

### Bystander nofailure rule

A bystander is `host_reboot` set and `find_culprit` returned `None`. That includes a real sudo shutdown this card did not match, and a reboot with an empty journal.

The rule is only this branch. It does not apply to the culprit.

- Status becomes the run's source phase: `review` when that run's `claimed` event has `source_status=review`, otherwise `ready`. Claim, pid, and fingerprint are cleared.
- `consecutive_failures` is not incremented. `_record_task_failure` is not called. The id is not auto-blocked and is not in the list `detect_crashed_workers` returns.
- The closed run's outcome and the board event kind are `host_reboot`. The event payload has `host_reboot`, `spawn_boot`, `current_boot`, and `retry_status`, and no `reboot_culprit`.
- `last_failure_error` is the reboot head ("host rebooted under worker pid … (boot <8> -> <8>); requeued without counting a failure.") plus the classifier text, sliced to 500 characters.
- The id is appended to `sweep.host_rebooted` and exposed as `detect_crashed_workers._last_host_rebooted`. Nothing in `DispatchResult` receives that list.
- This branch is checked before `rate_limited`. A bystander is not also recorded as rate-limited, even if the classifier said quota wall.

The culprit is the opposite on the counter and the status. Its run and event are still `host_reboot`, but it is appended to `crash_details`, so `_record_task_failure` runs with `force_trip`, `end_run=False`, and `outcome="crashed"`. Because the run was already closed, that outcome argument does not rewrite the run. The trip sets `status` `triage`, `block_kind` `capability`, increments `consecutive_failures` by 1, writes `last_failure_error` (same 500-character cap, text starts with "THIS card rebooted the host" and the journal line), and appends a `gave_up` event with `sticky` true and `reboot_culprit` set to the match dict. `comment_culprit` then comments on the card and on every `task_links` parent and child, as author `dispatcher`. A comment error is logged and does not undo the trip. `recompute_ready` promotes only `block_kind` `transient` triage rows, so a `capability` trip is outside that promoter. The host-reboot tests do not call `recompute_ready`.

`_account_crashes` checks `protocol_violation` before `reboot_culprit`, and the reclaim split checks `rate_limited` before `crash_details`. A culprit that the classifier also marked rate-limited is requeued as rate-limited and not capability-blocked. A culprit also marked protocol-violation takes the violation budget instead of the immediate capability trip, and `comment_culprit` does not run. After a reboot the usual classifier result is `unknown` ("pid N not alive"), which is the path the integration tests cover. Those two overlaps are untested.

## Code

- `hermes_cli/kanban_host_reboot.py` — boot compare, journal parse, victim count, `note`, `find_culprit`, `comment_culprit`. `HOST_REBOOT_OUTCOME` is `"host_reboot"`.
- `hermes_cli/kanban_db_dispatch_dead_worker.py` — `_DeadWorker` moved here. New fields: `host_reboot` (default false), `reboot_culprit` (default `None`). `run_outcome` returns `host_reboot` when the flag is set.
- `hermes_cli/kanban_db_dispatch.py` — `_reclaim_dead_workers` selects `workspace_path`, counts victims, calls `note`, and parks bystanders. `_account_crashes` force-trips a culprit as `capability`. `detect_crashed_workers._last_host_rebooted` holds bystander ids.

```python
if dead.host_reboot and not dead.reboot_culprit:
    # source phase already restored; do not count a failure
    sweep.host_rebooted.append(row["id"])
elif dead.rate_limited:
    sweep.rate_limited.append(row["id"])
else:
    sweep.crashed.append(row["id"])          # culprits land here
    sweep.crash_details.append(...)          # force-trip if reboot_culprit
```

## Tests

Source file: `tests/hermes_cli/test_kanban_host_reboot.py` (7 tests). Executed against the fully applied public candidate with the canonical `scripts/run_tests.sh` runner: **7 passed**. The coverage descriptions below name the source assertions; live-journal and other explicitly untested cases remain untested.

Unit (no dispatcher):

- `test_parse_power_commands_keeps_only_executed_power_commands` — keeps an executed `shutdown -r`; drops a `command not allowed` shutdown and `systemctl restart`; keeps `systemctl --no-wall reboot`; drops `systemctl status reboot.target`.
- `test_boot_compare_needs_both_ids` — `boot_of` on a fingerprint, on an int, `None`, and `unverified`; `rebooted_since` true only when both ids are known and differ; empty current boot is not a reboot.

Integration, via `detect_crashed_workers`, with the pid reported dead, crash grace 0, current boot and Hermes home stubbed, and the journal reader stubbed:

- `test_culprit_in_its_workspace_is_blocked_and_bystander_requeued_uncounted` — workspace `PWD` → culprit `triage` / `capability`, run outcome `host_reboot`, journal command in `last_failure_error`, dispatcher comments on the culprit and on a linked card. The other card is `ready`, `consecutive_failures == 0`, run outcome `host_reboot`, error text contains "requeued without counting a failure", event kind `host_reboot` and not `crashed`. The reader is called once, with the spawn boot's dashes removed.
- `test_profile_scratch_matches_only_the_profiles_sole_victim` — scratch `PWD` blocks the assignee's only victim (`capability`); a different assignee's card is `ready`.
- `test_profile_scratch_with_two_victims_of_that_profile_blocks_neither` — same assignee, two victims, scratch `PWD` → both `ready`, `consecutive_failures == 0`.
- `test_plain_reboot_requeues_everything_uncounted` — empty journal → `ready`, `consecutive_failures == 0`, run outcome `host_reboot`, `_last_host_rebooted` is that id.
- `test_same_boot_death_is_still_a_crash` — same boot id → outcome `crashed`, `consecutive_failures == 1`, journal reader not called.

The four tests that require the dispatcher hunks are the workspace, sole-scratch, two-victim, and empty-journal cases. The parse and boot-compare tests exercise `kanban_host_reboot.py` alone. The same-boot test expects the pre-existing crash path.

Not asserted by this file, and not executed here: a live `journalctl` against a previous boot; nonzero `journalctl`, timeout, or a missing binary; the `task_id` path-component match; symlink or non-normalized paths; `shutdown -k`; a non-sudo reboot; `review` as the restored source phase; the culprit's `consecutive_failures` increment; `recompute_ready` on a later tick; `DispatchResult` ignoring `_last_host_rebooted`; comment-write failure; grace-period cards counted as victims but not reclaimed; `protocol_violation` or `rate_limited` overlapping a culprit; macOS or a host with no `/proc` boot id; a container restart that keeps the same boot id.

## Applying

`git am -3` patch 0958 in series order. It adds `hermes_cli/kanban_host_reboot.py`, moves `_DeadWorker` into `hermes_cli/kanban_db_dispatch_dead_worker.py`, and edits `hermes_cli/kanban_db_dispatch.py` (import, victim count, `note`, bystander branch, culprit trip, `_last_host_rebooted`). This public patch also carries historical merge-resolution reconciliation, documented in [mirror-958-verification.md](mirror-958-verification.md). The new module alone does not change the sweep; the dispatcher hunks are what rebook the run. If the patch no longer applies, re-implement from Behavior and Code. Do not retry a card whose `last_failure_error` says it rebooted the host until the shutdown command is gone.

## Gotchas

- Journal failure is a bystander, not a crash. A nonzero `journalctl` is cached as "no power command", so the card that ran the shutdown is requeued with no failure counted and can reboot the host again. The dispatcher user must be able to read the previous boot's sudo journal (`journalctl -b <boot> _COMM=sudo`).
- Unknown boot id is the other direction: the death stays `crashed` and a failure is counted. An unreadable `/proc/sys/kernel/random/boot_id`, an `unverified` fingerprint, or a legacy row is never upgraded to `host_reboot`.
- `journalctl` runs inside the reclaim write transaction (10s timeout, and a timeout is not cached). A stuck journal holds the board write lock, and each later card on that boot retries.
- Scratch attribution is a sole-victim heuristic, not proof. Two cards of that profile disable it. One card plus an operator shell in that profile's scratch can block the card. Workspace and path-component matches can likewise blame a card because someone ran sudo from that directory.
- `shutdown -k` and `init 0` / `init 6` match. A reboot that did not go through sudo matches nothing.
- Bystanders are invisible on `DispatchResult`. They are only on `detect_crashed_workers._last_host_rebooted`. Culprits appear in the crashed return list even though their run outcome is `host_reboot`.
- The nofailure rule is the bystander branch only. The culprit's counter is incremented and the card is tripped; do not treat every `host_reboot` run as uncounted.
- Comments name the command and `PWD`. A missing comment does not roll back the block. The 500-character `last_failure_error` cap keeps the prepended journal head and can clip the classifier tail.

## Plugin?

No. Core dispatcher internals (`detect_crashed_workers` / `_reclaim_dead_workers`). Not a plugin, and not loaded through the plugin path.
