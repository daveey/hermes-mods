# chat-closeout: cross-pass remote inventory gate

`remote_cards()` (and through it `read_cards`, `healthy()`, the owner wake's `parked_waits`) reads core's origin-ledger
+ hub-replica projection (`ksa.remote_links_and_origin_rows`). Core keeps that projection `_LINKS_MAX_AGE_S` = 15 s,
half this thread's 30 s tick, so every pass re-projected for an unchanged answer: native replay V2 measured
`remote_cards` 249 ms of 512 ms and `parked_waits` 84 ms (`NATIVE_REPLAY_REPORT_V2.md`).

The gate (`_gated_inventory`, `_gate_hit`, `_gate_project` in `__init__.py`) reuses **core's own last projection**
only while it is provably what core would project now. It has no age-based reuse and no whole-tick skip.

## What a reuse requires (checked on every call)

| Input core's projection reads | How the gate checks it is unchanged |
|---|---|
| hub replica task/gateway rows, every local board | core `_links_fingerprint()` (its own content keys) |
| origin ledger | core fingerprint (stat) **and** the retained observer's `data_version` (`core.revision_token`) |
| profile/gateway scope | core `_origin_scope()` |
| state.db compression tips | resolved again with one short-lived connection (never retained, IDLE-READS.md), only for the sessions core's `superseded` can resolve (`_alias_sessions`) |
| replica sync meta | `last_ok` / `offline_since` re-read; every clock verdict (project() 180 s freshness, replica stale > `HUB_STALE_AFTER_S`) re-judged at the current time |
| owner liveness windows | every scope heartbeat `observed_at` and its `scope_window` end, every obligation's owner observation and its 180 s end: a reuse never crosses one |

`synced_at` follows `last_ok` (every hub pull) without any verdict changing; a hit restamps it the way core does
(origin rows: `last_ok`; live replica cards: `min(owner seen, last_ok)`), or projects when it cannot be derived.

## What is never stored

- A projection core served from its own cache (`entry is before`), or projected while any input moved (inputs read
  under core's `_links_lock` before, compared after, plus the replica clock before/after).
- A replica read that failed (core kept `_remote_last`: the stale fallback), or a fresh replica whose rows are all
  not current (project() may have swallowed a replica error).
- Anything when an outer `one_annotation_snapshot` is active (core pins its own projection there), or when core
  lacks any attribute the gate keys on (older cores: plain core call).
- Core `_reset_cache_for_tests` / no `_links_cache` entry for the memo's origin scope drops reuse (core keeps one
  entry, or a dict keyed by origin scope).

Wait ages (`describe_replica`) stay outside: `parked_waits` computes them from the gated rows at call time.

## Input-key shortcut

Before `_gate_hit`, `_gate_fast` reuses the last validated memo while cheap content keys of every input above are
unchanged (IDLE-READS.md "Unchanged ticks"); the clock verdicts and cut-offs are still judged now. Any key change,
an unreadable key or a crossed window sends the pass through the full gate.

## Parked waits (owner wake)

`_parked_waits` composes core `parked_waits` from core's own helpers instead of calling it:

- Remote cards: the gated projection above (`_gated_inventory`), then core `_remote_wait` per card at call time.
- Local cards: per board, the clock-free rows (`_parked_pairs`, `_local_wait_rows`, titles) come from the board's
  registered retained observer (`core._structural_reads`, guarded `open_ro`) and are reused while its
  `data_version`, file identity and `-shm` generation are unchanged. `_describe_waits` (waiting vs stalled) runs on
  every call with the current time.
- A park-event query failure on a board that has `task_events` (core swallows it into status-only info) raises
  `_Uncacheable`: that pass answers from a one-shot read with core's degradation and nothing is stored. A board
  read failure skips the board, as core does. Older cores (missing helpers): core's own `parked_waits`.

Tests: `tests/test_remote_gate.py` and `tests/test_parked_local.py` (real fork core; `HERMES_CORE_SOURCE`). Each
guard has a mutant that fails it, except the post-projection replica-clock re-check in `_gate_project`: with core's
project() freshness and `HUB_STALE_AFTER_S` both 180 s, any verdict flip during a projection with origin rows also
makes every row stale, which the swallowed-error guard already refuses. It stays as defence in depth.
