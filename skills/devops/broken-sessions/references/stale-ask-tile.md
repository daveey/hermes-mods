# A chat still shows a blocked card or ask after it was withdrawn or answered

Answer the user from the hub's records first, then look further.

1. Find the records for the request or step id:
   `rg -l '<requestId>|<us_ step id>' ~/.hermes -g '!cache/**' -g '!sessions/**'`.
   This is faster than querying each board with sqlite, which can time out. Open live DBs only through
   `~/.hermes/bin/hermes-core-python -m sqlite3`.
2. Read what the hub recorded:
   - `user-steps/<session>.json`: each step's `status`, `withdrawn` and `resolution`, plus the chat's linked `card`.
   - `session-panel/feed-serve-*.json`: this file is JSONL. Parse the last line that names the session. Its
     bundle carries `cards`, `tasks` and `requests[].state`, which is what the panel renders.
   - A withdraw that returns 409 "already closed" means the request is already closed.
3. If all of these are clean, the stale tile comes from the client. Read the user's Desktop DOM
   (skill inspecting-hermes-desktop-dom) to get the tile's id and title before telling the user it's gone. If you
   can't reach that Desktop, file a fix card that names the clean hub evidence.
