# Idle process-output tail backoff

Patches: 3834.

## Behavior

A quiet detached output log is polled progressively less often, while arriving output resets the short interval and a finish request can interrupt the idle wait immediately.

## How it works

The output-log reader begins with the existing 0.2-second interval, doubles after empty-read waits and caps at two seconds. Data resets it. Waiting uses `_reader_finish_requested.wait` rather than unconditional sleep, so poll/wait reconciliation can terminate the tail without waiting for the cap. Output is still ingested on the next poll and normal process-fate settlement remains in place.

## Code

- `tools/process_registry_output_log.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/tools/test_process_output_log_idle_backoff.py` (patch 3834).

- **3834:** Scripted empty reads require the 0.2/0.4/0.8/1.6/2.0 capped wait sequence; late output is ingested on the next poll and resets the wait; a real finish-event test joins the reader promptly.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

New output after a quiet spell can wait until the next capped poll; this is a deliberate latency/idle-work trade-off. Immediate finish refers to the explicit finish event, not instantaneous detection of every external process exit. No fresh CPU-reduction measurement was made here.

## Plugin?

No. The registry's tail loop and finish event are internal process lifecycle state.
