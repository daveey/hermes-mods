# Native annotation refresh and part-specific invalidation

Source patch ordinals: 5749, 5751–5754, 5766–5770, 5792, 5806.

## Behavior

The native annotation holder wakes for source changes, declared clock deadlines and ownership transitions, rather than rereading inputs on lease heartbeats. It retains trailing confirmation reads for commit-visibility races and retries failed coverage/read work. Refresh planning rereads only affected annotation parts when their keys can vouch for them.

## How it works

Linux `FileEvents` installs inotify coverage before snapshot reads, includes absent ancestors and SQLite sidecars, preserves old watches on failed replacement, and uses `poll` for high descriptor numbers. Source events are distinct from demand-only control wakes; reader close notifications are excluded. One-shot settlement deadlines confirm WAL visibility without becoming a permanent poll.

`kanban_annotation_parts` keys contributions by relevant content revisions and declared files. Provider wall deadlines, unknown board evidence and failed reads force conservative work. Counts renew using each scope's own read age. Declared done-check Board/replica inputs use content revisions; one optional read pass spans a count, and a failing pass close does not erase verdicts. Native refresh no longer builds the legacy narrow inventory or claims its extended lifetime.

## Code

- `hermes_cli/annotation_event_loop.py`
- `hermes_cli/annotation_file_events.py`
- `hermes_cli/kanban_annotation_host.py`
- `hermes_cli/kanban_annotation_parts.py`
- `hermes_cli/kanban_session_activity_attention.py`
- `hermes_cli/kanban_external_activity.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

Source suites cover idle/no-read behavior, trailing writes, lost/regained ownership, wait failures, watch replacement/overflow, symlink and ancestor transitions, high descriptors, WAL commit races, failed coverage installation, scoped retries, done-check deadlines, part/full-read equivalence, per-scope count renewal, close failures and native-versus-legacy publication.

See `tests/hermes_cli/test_annotation_event_loop.py`, `test_annotation_event_loop_rearm.py`, `test_annotation_file_events.py`, `test_annotation_native_holder.py`, `test_annotation_native_prepare.py`, `test_annotation_attention_events.py`, `test_annotation_done_events.py`, `test_annotation_event_sources.py`, `test_kanban_annotation_parts.py`, and `test_kanban_external_activity.py` in the same directory.

## Applying

Apply the ordered holder, event-source and part-planning changes together. Providers declare `watch_paths` and `next_deadline` through the registration boundary; event declarations must enumerate dependencies without querying their databases. The later refresh-floor change is documented in the companion refresh-window feature.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

Native support here is Linux inotify; unsupported platforms fall back to authoritative request reads, not disguised native polling. Undeclared providers retain a logged bounded fallback. Content revision keys cover their declared table sets, not arbitrary tables a plugin might read. Coalescing permits bounded display delay; it is not an atomic cross-store snapshot.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
