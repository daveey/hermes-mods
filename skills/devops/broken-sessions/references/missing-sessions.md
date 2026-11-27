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
   `desktop_stripe_report` lines is a new chat whose first prompt never reached the hub (a row is created on
   first prompt). Do not call it "never sent": the text may sit in the app's retry draft (step 4). An id with
   `tui prompt accepted`/`turn finished` but no row lost its record.
4. **Client-side copies.** In the app dir: `transcript-cache.sqlite` (`entries` + `bodies`: per
   connection/profile/session transcript cache), `sidebar-boot-snapshot.json` (last sidebar tree),
   `prepared-submissions-*.json` (queued unsent prompts with target session). These can hold content the
   server lacks. **Local Storage** (`Local Storage/leveldb`) is the authoritative record of what the composer
   sent: `hermes.desktop.pendingSubmissions.v2:[sid,id]` (every prompt with its target session),
   `hermes:composer-draft-retry:v1:<sid>` (text of a send that failed, with attachment ids) and
   `hermes:composer-outbox:v1:[sid,id]` (emptied when the app gave up). Grepping the `.ldb` bytes misses
   snappy-compressed blocks and the CDP/localStorage route needs the renderer: decode with
   `scripts/read_desktop_localstorage.py '<key regex>'` (stdlib, read-only). Stage it on the hub in
   `~/.hermes/tailnet/files/` and `tailnet_copy put` with `local_name` = the bare file name; inline base64 in
   `tailnet_exec` hits its 4000-char command cap.
5. **User gives a list of what they typed** (dictation history, paste): trace every item. Pick a distinctive
   phrase each, query `messages` (role `user`, today) joined to `sessions` for session/title/archived; misses go
   to step 4's `pendingSubmissions`/`composer-draft-retry` keys and the desktop.log errors at that minute.
   Several dictations can arrive as one message, and an item may sit in an older chat that was open then.
   Report a per-item table plus the full text of anything not on the hub.
6. Report a table per session, then offer the concrete restore (unarchive, copy between connections) and wait
   for the pick before writing. Never restore while that ask is still open: the user may choose to keep the
   sessions archived, and the change then has to be undone.

**Hub SQLite:** the sqlite-live-guard blocks the system `sqlite3` CLI and `python3` (old SQLite with the WAL-reset
bug) on live DBs, including `-c` one-liners. Put the query in a scratch `.py` file opening
`file:...state.db?mode=ro` and run it with `~/.hermes/bin/hermes-core-python <file> "<sql>"`;
`hermes-core-python -m sqlite3` mis-parses a `file:` URI as a plain path ("no such table").
