# Memory capacity feedback and consolidation headroom

Patch: `0098`.

## Behavior

Near-full `memory` and `user` blocks show the number of free characters in their
system-prompt header and advise putting removes or shortening patches in the SAME
batch as growth. Overflow errors for add, patch, replace, and batch writes suggest
freeing enough space to leave roughly 10% headroom, rather than only enough for the
current write. Limits and atomic batch semantics are unchanged.

## How it works

`HEADROOM_FRACTION = 0.10`; `_headroom_floor(limit)` is
`int(limit * (1 - HEADROOM_FRACTION))`. The header warning appears only when usage
exceeds that floor and the limit is positive. Free space is clamped to zero.
Overflow hints calculate the amount to free from the proposed total minus that
floor. The tool schema's IF FULL guidance tells the model to inspect the header
and consolidate on the first call. Nothing automatically removes stored facts.

## Code

- `tools/memory_tool_store.py`: headroom helpers, `MemoryStore` mutation failures,
  and `format_for_system_prompt`.
- `tools/memory_tool.py`: `MEMORY_SCHEMA` capacity guidance.
- `tests/tools/test_memory_tool.py`: overflow, batch rollback, and header tests.

## Tests

```sh
scripts/run_tests.sh tests/tools/test_memory_tool.py
```

The diff extends `TestMemoryStoreReplace` to check both the minimum space and
headroom suggestion, including unchanged state after an over-budget batch.
`TestMemoryStoreSnapshot.test_header_calls_out_free_chars_when_nearly_full`
checks roomy versus tight headers. This file passed in the full-series regression run (October 16, 2026 PT).

## Applying

Apply the ordered patch prefix `0001–0098` from this mirror (or the complete
`0001–0105` series) to the fork's documented merge-base in a disposable checkout.
This feature is patch `0098`; isolated application is not claimed or tested here.
Run the tests above from the resulting Hermes source root, not this mods repository.

## Gotchas

- The 10% target is advice, not reserved capacity or a lower hard limit.
- Character usage includes the stored entry delimiters; count more than just the
  new text. The header reflects the memory snapshot being formatted, not a live
  refresh after every tool call.
- Memory batches check the limit on the final result. Do not confuse this with
  skill-file batches, whose growing write must follow the shrinking write.

## Plugin?

No. Both prompt formatting and capacity errors belong to the core memory store;
a plugin cannot replace these paths through an ordinary tool handler.
