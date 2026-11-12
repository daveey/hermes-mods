# Final-job idle sweep

`sweep_jobs` keeps bounded, process-local hints for **proven final, no-work** jobs.
It does not cache whole sweeps or the result of calling `watch_once`.

## Decision boundary

- A final card is eligible only when `_completed_card_needs_sweep` successfully
  reports no work. A later block or comment change invalidates the hint.
- A final session is eligible only with a readable completion receipt whose status
  is `admitted` or `missing`. `completion.deliver` already treats that receipt as
  terminal before looking up a session authority. No session DB lookup is needed
  for this decision. Pending/backoff records and a delivery attempt are not receipts.
- Nonfinal jobs still run clock/process checks. Unavailable/stat/parse/DB checks
  never publish a hint. Reconciliation attempts are inspected again on the next pass.
- A final job whose decoded owner has nothing to reconcile (no owner, `relayed`, an
  unknown kind, or a card lacking a task id or board path) is proven no-work from its
  job files alone: `watch_once` would return without effect, so no board is opened
  and the hint's key is the job-file signatures. Keep `_sweep_nondeliverable` in step
  with the early returns of `reconcile_closed_ask`, `reconcile_completed_card` and
  `reconcile_session`. A malformed state file still runs the unchanged legacy pass.

The key is captured **before** successful inspection: status, meta, legacy owner,
final result, attachment and completion sidecars, plus the owner board DB/WAL
signatures. File identity, size, nanosecond mtime and ctime participate. Removed
sidecars and atomic replacements invalidate the key.

Board observers additionally read `PRAGMA data_version`: WAL bytes may precede
commit publication, so unchanged DB/WAL stat values alone are insufficient.
Observers are query-only, hold no transaction, are serialized across sweep threads,
and have a generation so reopening cannot accidentally reuse a version number.
Each board is observed once per sweep. Changes during inspection are detected on
its next sweep, rather than tagging an old decision with a new signature.

WAL generations: observers open through core's guarded, registered reader
(`kanban_generation.open_ro`) and `_sweep_evict` is a registered core reader cache,
so core `recover()` closes them before reopening a board whose `-wal`/`-shm` were
unlinked; a busy observer raises `DeletedWalGenerationError` (never waits) and the
caller retries. Each observer also records its `-shm` identity and is dropped when
the live one differs, which is the only fence on cores without the registry.
Malformed or unreadable state files and receipts keep the legacy quiet fallback
decision but are never memoized, so they are re-read every pass without new logs.
`skip` is compared as an absolute path, like the enumerated jobs.

Bounds: 4,096 final-job hints and 32 board observers; observer eviction closes its
connection. Eviction can cause extra inspection, not suppress work. Job enumeration
and stat calls remain; steady cache hits do not read text, create directories or
open SQLite connections. Concurrent sweeps coalesce while one sweep is running.
There is no persistent cache and no change to the delivery/reconciliation protocol.

Enumeration cost: a host keeps every settled job directory (hundreds after a few weeks), and each
pass used to build a `Path` per state file and run `Path.is_dir` plus eight `Path.stat` calls per job.
`_sweep_enumerate` lists the jobs root once with `os.scandir` (directories only, symlinks followed, an
unreadable entry is not a job) and keeps each job's `Path` and its eight state-file path strings in
`_SWEEP_PATHS`, keyed by job name, dropped when the name leaves the root and rebuilt when the root differs. Every
state file is still stat'ed on every pass and the signature tuples are unchanged; only path building and
pathlib overhead went away. `tests/test_idle_sweep_paths.py` pins the steady pass (no pathlib calls under
the root, one stat per state file), equality with the pathlib enumeration (files, symlinks, dangling
links), every kind of change being seen on the next pass, and two roots sharing a job name.

## Why this change

Repeated historical work came from `sweep_jobs` unconditionally reading every job's
status, then `_completed_card_needs_sweep` decoding its owner and opening SQLite on
every interval. The read-only filter prevented writes but retained no stable-input
no-work decision. Existing `test_repeated_sweep_never_takes_attachment_lock_after_resume`
checked write-lock avoidance, not repeated reads. The new idle regressions count
inspection and I/O, closing that detection gap.

## Delivery retries: one core store open per attempt

A session completion without a receipt is retried after every backoff (transient cap
120 s, blocked 600 s) and that is required: failures are never cached. Each attempt
used to open the profile `state.db` writable twice: `read_producer_admission`, then
`admit_producer_completion`, which repeats the same producer lookup inside its own
write transaction and returns an existing row instead of inserting. A gateway-routed
session with no live owner therefore paid two full `SessionDB` opens per retry.
`completion.admit_producer` now calls admission first. Its only divergence from the
read-first sequence is `ValueError` (an existing row with another payload, or invalid
text/producer); that falls back to the read and reproduces its outcome exactly. A
fresh admission is still read back before it counts.
`tests/test_admission_reads.py` counts one open per failed attempt (sweep and
`watch_once`) and compares outcome plus core rows against the verbatim previous
sequence for twelve cases (unrouted, routed, missing, no store, prior same/mismatch,
prior then routed, invalid text/producer, storage retry).

Cost that remains is core's: each writable open runs
`refuse_deleted_wal_generation`, a process-wide fd scan whose clean verdict is cached
30 s keyed by the DB directory mtime and sidecar identity. A store with no other open
handle deletes its WAL on close, changing that key, so every open rescans. The owning
gateway keeps its own handle open, so in production the scan is cached; a replay
that holds no handle measures an uncached scan per open.

## Verification

`tests/test_idle_sweep.py` covers unchanged card/session inspection counts, every
key file changing, real WAL late blocks, commit visibility with frozen stat values,
mutation during inspection, failure retry, nonfinal clocks/process checks, absent
DBs and replacement, cross-thread observers, pending/contended delivery, receipt
removal, deleted jobs, absent roots and `skip`. `tests/test_idle_sweep_generation.py`
covers sidecar loss then a core board connect, a polled sweep reconciling after
sidecar loss, recovery refusing a borrowed observer, the older-core `-shm` fallback,
relative `skip`, and malformed settled files re-checked quietly.
`tests/test_idle_sweep_owners.py` covers the no-owner proof: each non-deliverable
shape and final status inspected once with no board open, steady hits reading no
text, every key-file change, an owner gaining a card/board or becoming a session,
malformed and non-object files, injected owner-read failure, nonfinal ownerless
clocks, and a mixed corpus where only deliverable jobs are re-inspected.

Use an isolated HOME/HERMES_HOME/TMPDIR and `env -i`; pass the core checkout
explicitly to `scripts/test-all -k user-terminal --hermes-core <core>` and through
`HERMES_USER_TERMINAL_CORE_ROOT`/`PYTHONPATH`. The two standalone watchdog
subprocess tests (`watchdog_env` in `test_closed_asks.py`) never inherit
PYTHONPATH; when the resolved watchdog interpreter is the runner's own (an isolated
HOME has no committed core dependency environment), they forward only the runner's
`site-packages` dependency entries, never core. They also accept
`HERMES_USER_TERMINAL_WATCHDOG_PYTHON=<core>/.venv/bin/python`. This verifies the
standalone path with that interpreter, not the installed production launcher's
dependency setup. No live board, session, terminal or service is needed for these tests.
