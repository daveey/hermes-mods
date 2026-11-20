# Online FTS recovery and wholesale free gates

Mirror snapshot 5011; ordinals 4896, 4897, 4898, 4899, 4900, 4901, 4930, 4931, 4932, 4933, 4934.

Entries:

- **4896**: state: online stale-FTS recovery that needs neither an exclusive store nor a readable old index
- **4897**: state: FTS housekeeping chores move to gateway/run_housekeeping_fts; repair parsers to a helper; budget test on a fake clock
- **4898**: state: online FTS recovery frees healthy indexes and unlinks only damaged ones; review fixes
- **4899**: state: online FTS plan refuses what it cannot attribute; one-shot rebuild never drops a shared tree
- **4900**: state: FTS plan runs before the session lock, once per tick, and a refusal is remembered
- **4901**: state: FTS plan is per retry tick, made before the lock, never reused across attempts
- **4930**: state: every wholesale FTS page-freeing path asks the whole-file plan first
- **4931**: state: optimize_fts() 'optimize' asks the whole-file plan first
- **4932**: state: FTS free gate frees nothing while any family is named; cross-process refusal memo; per-thread plans
- **4933**: state: FTS gate keeps the schema memo, detaches under the lock, remembers only lasting refusals
- **4934**: state: an open-time refusal replay keeps the memo flag (debug, not ERROR)

## Behavior

- **Online recovery** (4896–4901): stale FTS recovery needs neither an exclusive store nor a readable old index. A whole-file integrity_check plans which FTS families to unlink; healthy families are trashed and drained; damage outside search (or an incomplete check) refuses and points at `hermes sessions recover`. One-shot DROP never frees a tree that shares a page with canonical data. Plans run before the session lock, once per retry tick, never reused across attempts; a 6 h refusal memo stays. Housekeeping chores live in `gateway/run_housekeeping_fts.py`. CLI `--wait` never prints success it did not reach.
- **Wholesale free gate** (4930–4934): every page-freeing path (realign, trigram exclusion, orphan sweep, missing-trigger rebuild, `rebuild_fts`, optimize-storage truncation, FTS5 `optimize`, trash teardown) asks the same whole-file plan first. While any family is named, nothing is freed (detach / fail-open for online retry). Bounded `merge` is not gated. Plans are per-thread (WeakKeyDictionary, not `vars(db)`). Refusal memo is next to `state.db` (all processes); busy/locked/IO failures are not remembered. Open-time refusal replay logs at debug, not ERROR. `optimize_fts` raises `FtsFreeRefused` on a named family; returns 0 when the schema moved and there is no plan.

## How it works

- `hermes_state_fts_online.py` (`plan_online_recovery`, `plan_from_check`, `_fts_wholesale_free_allowed`, `recover_stale_fts_online`).
- `hermes_state_search.py`, `hermes_state_fts.py`, `hermes_state_schema.py`, `gateway/run_housekeeping_fts.py`.

## Code

Shipped non-test paths (union over these entries):

- `gateway/run.py`
- `gateway/run_housekeeping_fts.py`
- `hermes_cli/doctor_state.py`
- `hermes_cli/sessions_cmd.py`
- `hermes_cli/subcommands/sessions.py`
- `hermes_state.py`
- `hermes_state_fts.py`
- `hermes_state_fts_online.py`
- `hermes_state_maintenance.py`
- `hermes_state_schema.py`
- `hermes_state_search.py`
- `website/docs/reference/cli-commands.md`
- `website/docs/user-guide/session-storage-recovery.md`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_state/test_fts_free_gates.py`
- `tests/hermes_state/test_fts_online_recovery.py`
- `tests/hermes_state/test_fts_tool_write_bounds.py`
- `tests/hermes_state/test_fts_trigram_cron_exclusion.py`
- `tests/hermes_state/test_fts_trigram_subagent_exclusion.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- A family named by integrity_check is not a license to DROP a shared page. Scoped checks cannot tell sharing; only a whole-file check can.
- Operator `--force` re-checks a remembered refusal. Automatic retry does not, for 6 h.

## Plugin?

None.
