# Completion releases cron ownership, not execution

Mirror ordinals: **2106–2111**. These changes form one ownership contract; the later hardening supersedes the initial draft.

## Behavior

Declaring a chat `done` normally releases its live cron jobs, including jobs owned by its conversation lineage. It clears `owner_session_id` and records `released_from` / `released_at`; it does not cancel the job or alter its prompt, schedule, delivery or execution settings. Paused, completed and disabled jobs are not released.

A done declaration with `keep_crons: true` opts out. Retention is persisted on each matching job as `owner_retained: <owner session id>`, not a boolean. The migration respects that retained owner even if a subsequent outcome replaces the declaration. Retention does not follow a job reassigned to another owner. A later explicit ordinary done declaration can release the retained job.

The sessions outcome audit reports `done_owns_cron` and the relevant jobs when a stored-done chat still owns live cron work. It does not silently migrate those jobs.

## How it works

`declare_outcome` first persists completion intent to the session database, then changes cron stores, then attempts to persist the release receipt. A failed first database write cannot release ownership. Cron failures leave the done intent intact and return `cron_release_error`; a failed receipt write also reports the completed release rather than pretending the first write never happened. These are separate stores, not one cross-store transaction.

`store_homes(db)` resolves the supplied database's installation home, root home and sibling profile cron stores. It does not use an unrelated ambient home. Each transition reads and revalidates ownership while holding that store's jobs lock. `_read_records` parses existing JSON without invoking repairing load behavior, validates the records and nonempty string IDs, and records the load stamp for the existing save protection. Malformed files are reported and left untouched. `_retain_session_jobs` isolates the owner-bound update.

The migration uses already-stored `done` outcomes (declared or derived), not a newly run prose judge. It skips unknown owners, non-done owners and owner-bound opt-outs. With an errors collector it continues across stores and returns successful work plus errors. CLI JSON contains `apply`, `jobs`, and `errors`, and exits nonzero when errors exist.

## Code

- `cron/session_ownership.py`: nonrepairing reads, store selection, locked release/retention, migration and module CLI.
- `hermes_state_outcome.py`: declaration validation, durable intent ordering, lineage release, receipt errors and removal of stale carried cron lists on refresh.
- `hermes_cli/sessions_cmd_outcome.py`: audit-only `done_owns_cron` reporting.
- 2106 introduces the draft; 2107 binds stores and persists intent first; 2108 reports partial migration; 2109 rejects repair and binds retention to an owner; 2110 extracts retention; 2111 adds boundary controls.

## Tests

Source regression tests were inspected, **not executed in this documentation task**.

`tests/hermes_state/test_outcome_cron_release.py` covers unchanged execution fields after release, explicit retention, failed declaration writes, corrupt-file preservation, cross-profile release without touching unrelated jobs, CLI dry-run/apply, partial apply with a corrupt later store, stale cron-list refresh, retention after outcome replacement, reassignment, failed receipt writes and malformed-store rejection across release/keep/dry/apply.

`tests/hermes_state/test_cron_ownership_controls.py` covers profile-to-root lineage, byte-stable repeated release, inactive jobs in declaration and migration entry paths, and owner reassignment while a release waits for the lock. These are source-level declaration/module-CLI tests, not proof of a live Desktop action.

## Applying

Apply the complete snapshot using the full replay recipe in [README.md](README.md), including its integration bridges and policy checks; do not cherry-pick these mail patches as an independent stack. Use the installed Hermes Python environment for any subsequent testing.

For existing ownership, preview before applying:

```sh
python -m cron.session_ownership --db /path/to/home/state.db
python -m cron.session_ownership --db /path/to/home/state.db --apply
```

The database must already exist. Inspect both the JSON errors and exit status; a nonzero apply can have released jobs in earlier stores. No migration or live cron mutation was performed for this doc.

## Gotchas

- Done is not cancellation. Stop or delete a schedule separately if execution must end.
- Dry-run does not repair legacy metadata. Fix malformed stores deliberately, not by expecting ownership operations to normalize them.
- Store-by-store changes can partially succeed. Re-run after resolving errors; already-released jobs are naturally skipped.
- The initial intent may be stored even when the final receipt write fails. The returned error matters.
- Migration considers stored outcomes only; it cannot infer completion for unknown or unresolved sessions.

## Could this be a plugin?

A plugin could offer migration UI or a preview command. Safe default release belongs at the core outcome persistence boundary, where intent ordering, lineage, locks and every declaration caller can share the same contract. A post-turn plugin alone cannot provide that ordering.
