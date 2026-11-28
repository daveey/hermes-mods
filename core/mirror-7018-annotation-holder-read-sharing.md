# Annotation holder counts custody and read sharing

Patch coverage: 6610, 6624, 6625, 6626, 6688, 6689, 6690, 6817, 6827, 6828, 6839.

## Behavior

The Kanban annotation holder (the thread that keeps per-session card counts, links and attention fresh) read too much per pass. These commits reduce work while keeping certification strict.

- The blocked-clock `MAX` query no longer scans unrelated newer history (a unary plus disables the min/max shortcut so the kind/task index is used); semantics are unchanged.
- Counts-only hub heartbeats keep their benefit when another part forces a combined read: the raw hub receipt is kept across a pass and the retained snapshot owns its execution, source age, generation and mutation cues. Legacy holders that never planned event parts delegate unchanged; planned holders keep strict age/completeness checks; a failed custody plan releases the annotation lock.
- Replacing superseded replica clocks: fresh `last_ok`/owner-heartbeat observations replace old expiries so old boundaries do not force all parts and start recurring clock chains. Input key probes may not borrow output clocks while only planning.
- Only the *uncertified counts part* is revoked when a normalized counts receipt loses its whole-pass raw fence; independent links/attention keys and waiting output survive.
- One forced links projection per holder pass; the pass links entry is shared with chat-closeout's gate only while it is still published (it must not be older than the gate's own input read). Session capture children reuse recent annotation copies.

## How it works

`kanban_annotation_counts.py` (`Source` custody: `begin`, `read`, `reused`, `failed`), `kanban_annotation_hub_key.py` (`Receipt`, `capture`), `kanban_annotation_deadlines.py` (`Schedule`/`Plan`, `replace`), `kanban_annotation_host.py` (`refresh_locked`, `replace_deadlines`), `kanban_annotation_parts.py`, `kanban_annotation_refresh.py` (`recent_copy`, capture role), `kanban_session_activity.py`/`..._revalidate.py` (`pass_links`).

## Code

`hermes_cli/kanban_annotation_{counts,deadlines,host,hub_key,parts,refresh}.py`, `kanban_counts_scopes.py`, `kanban_session_activity{,_revalidate,_timers}.py`, `sessions_capture_worker.py`, `sessions_native_source.py`.

## Tests

Read from source; **not executed here**. `test_annotation_counts_custody.py`, `..._counts_races.py`, `..._counts_metadata.py`, `test_annotation_deadline_custody.py`, `test_annotation_heartbeat_fence.py`, `test_annotation_part_custody.py`, `test_annotation_board_reuse.py`, `test_review_mutation_plan_recovery.py` (6626), `test_kanban_session_activity_timers.py` (6610), `test_kanban_annotation_parts.py` (6827/6828) and `test_sessions_capture_copy_first.py` (6839, the capture-child copy reuse; the sessions-capture-standby series is separate). CPU and context-switch figures in the commit text come from one deployment's profiling and are not reproduced. Untested: behavior under real multi-profile load.

## Applying

Apply in ordinal order (6828 corrects 6827; 6625/6626 correct 6624).

## Gotchas

- An unreadable mutation cue is a retryable failure, never a no-mutation certificate.
- Never hand a cross-pass gate an entry older than its own inputs.

## Why core, not a plugin?

Core session-activity and annotation internals.
