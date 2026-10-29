# Hot-path writable `SessionDB()` opens are named in the log; chat-closeout reads lineage read-only

Patches:

- `patches/0566-fix-state-name-call-sites-that-keep-opening-writable.patch`
- `patches/0591-perf-tui-kanban-notify-poller-skips-boards-whose-fil.patch`
- `patches/0621-perf-kanban-GET-board-reuses-its-read-while-the-boar.patch`

Plugin change (no patch, same commit): `plugins/chat-closeout/__init__.py` (`_lineage`, `_compression_tip`).

On 2026-10-27 the live Desktop backend (`hermes serve`, pid 95896) sat near 100% CPU from 15:16 on, 233
CPU-minutes in its first three hours. Its REST calls timed out ("Timed out connecting to Hermes backend after
5000-30000ms" in `desktop.log`, about 40 a minute at 16:14-16:19), including `GET /api/sessions/{id}` and
`/api/profiles/sessions/sidebar`.

## Root cause (five whys)

1. **Why were REST calls timing out?** The backend's worker threads were starved: a 5 s `sample` showed busy time
   spread over ~20 concurrent `Thread-N (_scan)` threads (top leaf `__proc_info`), `sqlite3Prepare` in the
   `chat-closeout`, `tui-notif-poller-*`, `kanban-unloaded-sweeper` and AnyIO worker threads, all contending for the GIL.
2. **Why were there ~20 `_scan` threads, numbered past 15,500?** `_scan` is the macOS deleted-WAL holder scan
   (`hermes_state_dbfile._iter_darwin_sidecar_holders`): it lists every open file of every process (1,515 pids,
   11,124 fds) in a daemon thread. It runs only from `refuse_deleted_wal_generation`, i.e. once per **writable**
   `SessionDB()` construction, and a scan that misses its 5 s join keeps running. `agent.log` has 332
   "deleted-WAL holder scan timed out" warnings that afternoon.
3. **Why so many writable opens?** Replaying Desktop's polled endpoints against the live `~/.hermes` with
   `refuse_deleted_wal_generation` spied: one `GET /api/profiles/sessions/sidebar` made **23** writable opens, all
   from `chat-closeout._lineage` → `SessionDB().get_compression_tip(sid)`, called once per finished card by the
   sidebar's waiting source (`pending_closeouts` → `session_proof`). The close-out thread's `owner_wake_tick` did the
   same through `_compression_tip`. `/api/status`, `/api/profiles/sessions` made none.
4. **Why is that expensive?** A writable open runs schema init and the holder scan: 58 ms CPU each on an idle
   process, versus 1 ms for `SessionDB(read_only=True)` (10 opens: 0.58 s vs 0.01 s). The handle was never closed.
   23 per poll is ~1.3 s CPU per pass, so the waiting source's 3 s cache expired before a pass under load
   finished, and passes ran back to back.
5. **Why wasn't it caught?** Nothing distinguishes a one-shot CLI open from a per-poll open: the plugin's tests stub
   `_lineage`, and core logs nothing about how often a call site opens writable handles. Core already records each
   handle's `_creation_site` but never uses it for rate.

## Behavior

- `chat-closeout` looks up a chat's compression tip with `SessionDB(read_only=True)` and closes it, in both the
  sidebar waiting source (`_lineage`) and the owner-wake tick (`_compression_tip`).
- `chat-closeout` reads the local boards once per pass (`_pass_reader`): the first row reads every id the ledger's
  pending rows hold and later rows take their subset, instead of opening every board once per pending row (45 rows
  on 2026-10-27). Ids outside the pass's union are read directly.
- The inbox list (`Inbox.list`, own home and `Inbox.for_home`), `session-mentions._sender_info` and
  `user-terminal._sender` / `caller_surfaces` read state.db read-only and close it.
- Core counts writable `SessionDB()` constructions per call site (`module.function:line`). A site that makes 30 or
  more within 60 s gets one WARNING naming it, the count, and the fix (read-only for reads,
  `hermes_state_registry.acquire()` for a long-lived writer); it is named again at most every 10 min while it stays
  hot. Read-only opens are not counted. Spread-out or distinct call sites stay quiet.

- Follow-up (py-spy of the relaunched backend, pid 14245, still ~2 cores at 20:30 with 21 open chats):
  - The per-chat Kanban notification poller skips a board whose `kanban.db`/`-wal` file signature (mtime_ns,
    size, inode) has not moved since that chat's last poll: no probe, no writable open, no claim. The signature
    is taken before the poll, so a commit during it (a new event, a claim, a refused-turn rewind) is polled on the
    next tick; consumer-health `polled` still fires on a skip.
  - hermes-mods `hub-sync` keeps decoded `published`/`replica` rows per collection while `hub.db` and its WAL are
    unchanged (file mtime_ns, size, inode), hands out shallow copies, and no longer mutates nested values in
    `_overlay`. `tasks()` 161 -> 31 ms, `sessions()` 87 -> 42 ms per call against the live 16k-row replica.

- Follow-up 2 (backend pid 85854 reloaded with all of the above at 21:32, still 36-47 s CPU per minute): the top
  REST cost was `GET /api/plugins/kanban/board?board=default` at ~1 s CPU per call (6.5 MB payload). Core 0621
  caches `get_board` per board path + query while `kanban.db`/`-wal` are unchanged (file signature taken before
  the read), at most 15 s old because diagnostics depend on the clock and config; a hit returns a copy with a
  fresh `now`. The endpoint returns `json.dumps` of the board directly (byte-identical to FastAPI's
  `jsonable_encoder` output, 16 ms instead of ~275 ms). Live replay, one poll every 2 s: 981 -> 199 ms CPU per poll.

## How it works

`hermes_state._note_writable_open(site)` keeps a deque of monotonic timestamps per site, trims it to the 60 s window
under `_writable_opens_lock`, and logs when the count reaches `_WRITABLE_OPEN_WARN_COUNT` and the site's last warning
is older than `_WRITABLE_OPEN_WARN_INTERVAL_S`. `SessionDB.__init__` calls it with `self._creation_site` just before
`_open_writer()`. Memory is bounded by the number of distinct call sites.

## Code

- `hermes_state.py`: `_note_writable_open`, `_WRITABLE_OPEN_*`, the call in `SessionDB.__init__`.
- hermes-mods `plugins/chat-closeout/__init__.py`: `_lineage`, `_compression_tip`, `_pass_reader`.
- hermes-mods `plugins/inbox/store.py`: `Inbox._reader`, `read_factory`, `for_home`; `plugins/session-mentions/__init__.py`:
  `_sender_info`; `plugins/user-terminal/__init__.py`: `caller_surfaces`, `_sender`.

## Tests

- `tests/hermes_state/test_writable_open_guard.py`
  - `test_warns_once_per_interval_when_a_site_reaches_the_threshold`: threshold, single warning, re-warn after the interval.
  - `test_spread_out_or_distinct_sites_stay_quiet`: no warning below the rate or across sites.
  - `test_writable_sessiondb_counts_its_caller_and_read_only_does_not`: the real constructor path.
- hermes-mods `plugins/chat-closeout/tests/test_lineage_readonly.py` (fails on the old code)
  - `test_lineage_follows_compression_with_a_closed_read_only_open`: the sidebar path, real `state.db`.
  - `test_owner_wake_tip_lookup_is_read_only_and_closed`: the owner-wake path.
- hermes-mods `plugins/chat-closeout/tests/test_waiting_source.py` (fails on the old code)
  - `test_local_boards_read_once_per_pass_with_the_same_answer`: one board open per sidebar pass, same answer as
    the per-row reader.

- hermes-mods `plugins/inbox/tests/test_list_read_only.py` (fails on the old code)
  - `test_list_is_read_only_and_closed[own-home|other-profile]`: both list paths Desktop polls.
- hermes-mods `plugins/session-mentions/tests/test_state_reads_read_only.py` (fails on the old code)
  - `test_sender_info_title_is_read_only`.
- hermes-mods `plugins/user-terminal/tests/test_state_reads_read_only.py` (fails on the old code)
  - `test_sender_title_is_read_only`, `test_caller_surfaces_session_source_is_read_only`.

- `tests/tui_gateway/test_kanban_notify_poller.py` (core 0591)
  - `TestUnchangedBoardSkip::test_unchanged_board_is_not_reopened_and_a_commit_is_delivered` (fails on the old
    code): no `connect`/`count_notify_subs` on an unchanged board; a completion is delivered on the next tick.
  - `TestUnchangedBoardSkip::test_skip_is_per_session`: one chat's signature never suppresses another's poll.
- `tests/plugins/test_kanban_board_read_cache.py` (core 0621)
  - `test_unchanged_board_is_read_once_and_a_write_is_seen`, `test_query_params_are_cached_separately`,
    `test_cache_expires_after_ttl`. The other 115 kanban dashboard plugin tests pass on the new endpoint body.
- hermes-mods `plugins/hub-sync/tests/test_hubview.py` (fails on the old code)
  - `test_unchanged_hub_db_is_decoded_once_and_a_write_is_seen`: no re-decode while unchanged, a replica write
    is read on the next call, a caller's mutation of a returned row does not reach the cache.

## Measured (replaying `/api/profiles/sessions/sidebar` every 2 s for 60 s in-process against the live `~/.hermes`)

| Plugin | Writable opens / min | CPU (one core = 100%) |
|---|---|---|
| before | 150 | 30% |
| read-only lineage | 0 | 22% |
| + boards once per pass | 0 | 17% |

The replay has no GIL contention from other threads, so it understates the live cost of each writable open. What
remains per poll is core `kanban_session_activity._legacy_remote_links` (~0.3 s CPU), outside this change.

## Gotchas

- Not changed: `tui_gateway/change_watcher._session_db_content_sig` re-hashes every `sessions` row (7.7k, ~40 ms,
  24 ms of it the fetch) on each state.db move, up to twice a second during a turn (~9% of busy samples). A time
  cap breaks its tested contract (a session change broadcasts on the next pass) and a cheaper aggregate pre-check
  would miss equal-length renames, so it was left as is.
- `plugins/hub-sync/tests/test_human_request_path.py::test_real_producer_serializes_human_request` fails on main
  before this change too (2 cases).

- Core `883b05f36b` (another session, same evening) caches a clean macOS holder-scan result and single-flights
  the scan, which caps the scan's share of the cost; this guard and the plugin fix remove the opens that drove it.
- The other plugins that opened a writable `SessionDB()` only to read now read read-only and close: the inbox
  list (polled per profile by Desktop), `session-mentions` sender titles, `user-terminal` caller surfaces and
  sender titles. Writers (inbox actions, slack-workspace escalations, runners repair) stay writable.
- `py-spy` needs root on macOS and `sys.remote_exec` needs a task port, so attribution used `sample` (C stacks with
  thread names) plus an in-process replay of the polled endpoints with the scan spied.
