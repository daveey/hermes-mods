# Incremental machine throughput refresh and loop error kinds

Mirror snapshot 4352; ordinals 4166, 4167, 4169.

Entries:

- **4166**: bound throughput refresh with transactional dirty-session history
- **4167**: isolate throughput floor and usage-index cardinality fences
- **4169**: loop accounting: record each pass error's exception class in a bounded error_kinds map

## Behavior

- **Incremental throughput** (4166, 4167): the machine throughput metrics previously re-aggregated every session and usage row on each refresh. A writer-side change journal now records which session keys changed so the reader can apply deltas to a bounded in-memory contribution memo instead of re-running the full SQL. Unsupported shapes, bound overruns (ids, models, rows), non-integral or negative values, SQL sum overflow, a replaced database file or any failure fall back to the original SQL and drop the memo. Test-only follow-up 4167 isolates the empty-journal-floor and usage-unique-index cardinality fences.
- **Loop accounting** (4169): each pass error records its exception class in a bounded `error_kinds` map next to `errors` in `counters()` and the snapshot file; at most 8 class names per pass (the rest under `other`), an error without a kind counts as `unknown`, and the map is created only on the first error.

## How it works

- `hermes_cli/session_throughput_revision.py` (new) installs triggers and tables (`throughput_revision`, `throughput_changes` ring of 65,536 entries, key cap 262,144) with an epoch, revision, nonce and floor; `install()` is atomic and idempotent, `settle_schema` is called from the state store's throttled heals and schema init in `hermes_state_schema.py`; `read_stamp`, `anchor_matches` and `changes_since` form the reader API; shape checks refuse unsupported schemas.
- `hermes_cli/session_throughput_repair.py` disables provably owned triggers stranded by partial protocol-table loss while preserving data.
- `hermes_cli/machine_throughput_incremental.py` holds the memo (limits 250,000 ids, 4,096 models, 1,000,000 rows) and `machine_throughput_metrics.py` reads inside one `BEGIN` under an `RLock`, checks the file's device/inode did not change and calls `forget` on errors.
- `hermes_cli/loop_accounting.py` gains `MAX_ERROR_KINDS`, `Pass.__exit__` naming the class and optional `error_kind` on `record()` / `span_close()`.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/loop_accounting.py`
- `hermes_cli/machine_throughput_incremental.py`
- `hermes_cli/machine_throughput_metrics.py`
- `hermes_cli/session_throughput_repair.py`
- `hermes_cli/session_throughput_revision.py`
- `hermes_state_schema.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_loop_accounting.py`
- `tests/hermes_cli/test_machine_throughput_incremental.py`
- `tests/hermes_cli/test_throughput_boundaries.py`
- `tests/hermes_cli/test_throughput_differential.py`
- `tests/hermes_cli/test_throughput_integration.py`
- `tests/hermes_cli/test_throughput_protocol_guards.py`
- `tests/hermes_cli/test_throughput_recovery.py`
- `tests/hermes_cli/test_throughput_revision.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- The journal adds triggers on tracked tables of the session store; tracking that is not exactly the expected shape stays full-SQL-only, which is safe but slower.
- `settle_schema` failures stay retryable and make the heal report incomplete.
- Counters recorded before this change cannot be attributed to exception classes.

## Plugin?

None.
