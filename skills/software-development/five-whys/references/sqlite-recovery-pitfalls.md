# SQLite recovery pitfalls

Reference for skill `five-whys`: its SKILL.md links this file from the step that needs it.

## Pitfalls

- For SQLite poll stalls, inspect transaction mode even when the SQL is SELECT-only. In rollback-journal mode, `BEGIN IMMEDIATE` followed by a no-op commit still requests an exclusive lock; an existing shared reader can strand it at commit and block new readers at their first PRAGMA/schema access. Reproduce with a held shared transaction on an isolated database. Move purely read-only methods to a query-only deferred snapshot, preserve immediate/FULL-sync mutation transactions, and test populated decoding plus connection cleanup. Do not identify the initial long-lived lock holder from this contention pattern alone.
- Recovering a damaged SQLite database by hand as the symptom fix. Several agents chasing the same alert
  each swap a rebuilt copy in, and every `mv` or sidecar `rm` under open WAL connections corrupts the next
  copy. For a Kanban board, the symptom fix is `hermes kanban [--board <slug>]
  recover` (single-owner lock; a second run names the holder's card, so comment there), proven by
  `sqlite3 -readonly <db> 'pragma integrity_check'` = `ok` plus its loss report. Other databases: the
  sqlite-live-guard `swap_db.py` helper. Never hand-run dump/`.recover` then `mv`.
