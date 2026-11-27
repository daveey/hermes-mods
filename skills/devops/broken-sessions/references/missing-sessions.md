# "My sessions disappeared": locate before recovering

Read-only until the user picks what to restore. Answer per session: stored / archived-or-hidden / on another
connection / never persisted.

1. **Which connection the app shows.** On the Mac running Hermes Desktop (`tailnet_exec`), read
   `~/Library/Application Support/Hermes/active-profile.json` (`defaultRoute.connectionId` + profile) and
   `connections.json` (every connection: `local`, ssh hubs/minis). Sessions live in the `state.db` of the
   connection that created them; a session made while another connection was active is not "lost".
2. **Stored records per connection.** Query each connection's `~/.hermes/state.db` and
   `~/.hermes/profiles/*/state.db` for recent `source in ('desktop','tui','cli')` rows: `id, title, archived,
   hidden, message_count, end_reason, started_at`. `archived=1` or `hidden=1` drops a session from the sidebar
   with its transcript intact; `end_reason` like `*_orphan_reap` is a reaper end, not an archive reason. There is
   no archive audit column, so do not claim who archived it.
3. **Ids seen but never stored.** Regex session ids (`YYYYMMDD_HHMMSS_xxxxxx`) out of `logs/gui.log*`,
   `agent.log`, `gateway.log`, then diff against `sessions.id`. An id that only appears in
   `desktop_stripe_report` lines was a new chat that never sent a message: a session row is created on first
   prompt, so unsent text is gone. An id with `tui prompt accepted`/`turn finished` but no row lost its record.
4. **Client-side copies.** In the app dir: `transcript-cache.sqlite` (`entries` + `bodies`: per
   connection/profile/session transcript cache), `sidebar-boot-snapshot.json` (last sidebar tree),
   `prepared-submissions-*.json` (queued unsent prompts with target session). These can hold content the
   server lacks.
5. Report a table per session, then offer the concrete restore (unarchive, copy between connections) and wait
   for the pick before writing.

**Hub SQLite:** the sqlite-live-guard blocks the system `sqlite3` CLI and `python3` (old SQLite with the WAL-reset
bug) on live DBs, including `-c` one-liners. Put the query in a scratch `.py` file opening
`file:...state.db?mode=ro` and run it with `~/.hermes/bin/hermes-core-python <file> "<sql>"`;
`hermes-core-python -m sqlite3` mis-parses a `file:` URI as a plain path ("no such table").
