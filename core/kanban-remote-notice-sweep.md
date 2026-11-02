# Remote notices: stale and superseded cards stop replaying

Patches:

- `patches/0969-fix-kanban-remote-notices-the-unloaded-chat-sweep-st.patch`
- `patches/0976-fix-kanban-recover-remote-notices-after-offline-gaps.patch`
- `patches/1063-fix-kanban-a-remote-card-s-notice-wakes-an-idle-chat.patch`

After a MacBook update the unloaded-chat sweep appended 293 "blocked" notices into about 216 idle chats for cards done days earlier. The hub client had switched task keys from `<board>/<id>` to `<profile>/<board>/<id>`; the frozen legacy replica rows looked live and the sweep lacked the freshness and ledger guards the live collector has.

## Behavior

- The sweep ignores superseded legacy replica rows, bounds terminal time with the freshness window and checks the delivered ledger (0969).
- 0976: owner timestamps reject notices more than six hours late and replica `updated_at` is server time, so a host baseline and immutable first-seen terminal times are persisted and shared by both consumers; terminal history is seeded on first run so offline gaps recover notices without replaying old ones.

## How it works

`tui_gateway/kanban_remote_observations.py` stores the baseline and first-seen times; `kanban_remote_notices.py` consults them.

## Code

`tui_gateway/kanban_remote_notices.py`, `tui_gateway/kanban_remote_observations.py`.

## Tests

`tests/tui_gateway/test_kanban_remote_notices.py`, `test_kanban_remote_arrival.py`, `test_kanban_remote_bootstrap.py`, `test_kanban_remote_observations.py`.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

None.

## Follow-up patch (1063)

**1063** (`0bc4df24bc1`, #220): two hub cards' done notices were written into an idle MacBook chat as passive `kanban_notice` rows on time, but no turn ran until an unrelated local card woke the chat 2-3.5 h later; the remote sweep only appended the row. The production sweeper (`kanban_unloaded`) now passes a `_RemoteWaker`: a fresh remote notice gets the local sweep's grace window (the chat's live poller claims first), is then claimed in the remote ledger the way `collect()` claims it, and starts ONE turn under the same skip rules, wake budget and slots. A refused or failed wake writes the passive row (`_persist_claimed`); a turn in flight releases the claim for the next pass. Calls without a waker (tests, tools) keep the passive-row behaviour.

Code: `tui_gateway/kanban_remote_notices.py` (`_settle_claim`, `_persist_claimed`, waker path), `tui_gateway/kanban_unloaded.py`. Tests: `tests/tui_gateway/test_kanban_remote_wake.py` (4). Tests were added or changed by these commits; they were not re-run for this mirror.

## Follow-up patch (1563): retained replica observers skip unchanged notice scans

**1563** (`1c57026fc2c`, `fix(tui): skip unchanged replica notice scans with SQLite observers`).

- **Behavior.** Every idle notice poll read the hub replica twice: `kanban_remote_notices._index` ran its task-row aggregate (`COUNT/MAX/TOTAL(seq)`), then `kanban_remote_observations.observe` took its snapshot. Now both readers first get the replica's change key. If it equals the key their memo was built under, they return the memo without opening the replica. Any commit, including a metadata-only one, is read on the next poll. An unchanged replica still gets claim release, re-offer and expiry.
- **How it works.**
  - `tui_gateway/kanban_replica_observer.py` (new) keeps one retained connection per resolved replica path: read-only, `query_only`, autocommit, at most 16 in an LRU.
  - `fingerprint(path)` returns `(path, (dev, inode), observer generation, PRAGMA data_version, (size, mtime_ns))`.
  - A file stat alone is not used as the key. A WAL commit writes its frames before it publishes them in `-shm`, so a stat taken in that gap matches a read of the old data; the module notes that 594 of 1000 cross-process commits were missed that way.
  - `data_version` is only comparable within one connection, so each key carries its observer's generation.
  - A replaced, recreated, missing or failing replica closes its observer and returns `None`, meaning read the replica and cache nothing.
  - Both readers keep a memo only when the key taken after the read equals the key taken before it.
  - `observe` also records which file (`file_identity`) its decoded rows came from, in `_decoded_files`. A commit that races a read keeps those rows, since their seqs still gate re-decoding. A replaced file or an unknown key discards them.
- **Code.** `tui_gateway/kanban_replica_observer.py` (new), `tui_gateway/kanban_remote_notices.py` (`_index`, `_reset_for_tests`), `tui_gateway/kanban_remote_observations.py` (`observe`, `_forget_decoded`).
- **Tests** (`tests/tui_gateway/test_kanban_remote_idle_read.py`; not run for this mirror):
  - Unchanged replica: `test_unchanged_replica_is_not_opened_by_either_reader` (`delete`, `wal`, `wal-writer-open`), `test_unchanged_replica_still_releases_reoffers_and_expires_claims`.
  - Commits reach the next poll: `test_every_commit_is_read_on_the_next_poll`, `test_metadata_only_changes_are_read`, `test_commit_during_a_read_is_read_on_the_next_poll`, `test_wal_commit_published_after_its_frames_is_read_on_the_next_poll`, `test_commits_from_another_process_are_each_read_after_they_return`, `test_commit_racing_a_read_keeps_unchanged_decoded_rows`.
  - Replacement and isolation: `test_replaced_replica_is_reread_even_when_seqs_match` (replace/recreate x equal/moved aggregate), `test_replaced_ledger_is_rewritten_on_an_unchanged_replica`, `test_replicas_at_different_paths_do_not_share_results`.
  - Failure recovery: `test_failed_reads_recover_and_unstatable_replicas_are_always_read`, `test_observer_failure_is_read_uncached_and_recovers`.
  - Observer lifecycle: `test_observer_is_reused_and_holds_no_snapshot`, `test_replaced_or_missing_replica_reopens_its_observer`, `test_observers_are_bounded_and_eviction_keeps_decoded_rows`.
- **Applying.** Depends on the preceding series; apply the full ordered series as described in the README "Applying" section. Applying this patch on its own is not certified.
- **Gotchas.** Each watched replica holds one open file descriptor for the life of the process (up to 16). The observer connection never leaves a read transaction open (`_data_version` raises if it does), so it does not pin a WAL snapshot or block checkpoints.
