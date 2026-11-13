# Kanban scratch GC refusal memo hardening

Patches: **3710–3711**.

## Behavior
- A malformed entry in the scratch archive-refusal memo (non-numeric `retry_at`, bad or missing `count`, wrong-typed fields) is dropped with one warning per load instead of aborting the board's whole `hermes kanban gc` pass. The dropped tree counts as never refused, so all removal guards still run on it.
- A huge hand-edited `count` no longer stalls the pass: the count saturates at 64 and the loader drops entries above it.

## How it works
3710 makes `Refusals` validate each loaded entry against what `record()` writes: finite, formattable `last_at`/`retry_at`, integer `count` of at least 1 (not bool), list-or-null `fingerprint`, string-or-null `reason`. 3711 adds `MAX_COUNT` (64; the backoff cap is reached at 4) so `record()` never computes a huge `2 ** count`, and the loader agrees with written state.

## Code
- `hermes_cli/kanban_scratch_backoff.py`
- `tests/hermes_cli/test_kanban_scratch_gc_backoff.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_scratch_gc_backoff.py`: `test_malformed_memo_entry_is_dropped_and_the_aged_pass_completes`, `test_memo_loader_drops_each_malformed_field`, `test_record_saturates_count_so_the_backoff_exponent_stays_small`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Custody, owner-unverified and protection guards are unchanged.
