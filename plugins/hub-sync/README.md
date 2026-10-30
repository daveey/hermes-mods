# hub-sync

REST over this gateway's [hermes-hub](https://github.com/daveey/hermes-hub) replica (`hub.db`), so the Desktop can
show and act on sessions, Inbox cards and Kanban cards of gateways that are offline right now. Reads are local and
keep working with the hub down; writes are owner requests that queue locally and are sent when the hub is reachable.
Without hermes-hub installed every GET answers `200 {available: false}` with empty lists, and the Desktop surfaces
built on it show nothing extra.

No agent hooks or tools: `register()` is a no-op; everything is `dashboard/plugin_api.py` → `hubview.py`.

## Endpoints (`/api/plugins/hub-sync`)

| Method | Path | Answer |
|---|---|---|
| GET | `/status` | `{available, gateway, synced_at, stale, gateways:[…], last_error, requests_pending, …}` |
| GET | `/sessions[?gateway=&exclude=a,b&archived=1&limit=]` | `{sessions:[row + gateway/key/version/local/stale/synced_at/pending]}` |
| GET | `/inbox[?gateway=&exclude=&all=1]` | `{items:[…]}` (open/queuing cards unless `all=1`) |
| GET | `/tasks[?gateway=&exclude=&board=&all=1]` | `{tasks:[…]}` |
| GET | `/where/{session_id}` | `{hits:[{gateway, profile, local, stale, synced_at, …}]}` |
| GET | `/session-links/{session_id}[?ids=a,b&origin_profile=work&origin_gateway=laptop]` | `{qualified_origins:true, origin_gateway, origin_profile, tasks:[{id, board, gateway, profile, ownership_state, via, local, stale, …}]}` |
| GET | `/requests` | what this gateway asked other gateways to change |
| POST | `/request` `{gateway, coll, key, action, args?, base_version?}` | `{id, queued_locally, state}`; 400 bad action/args/key shape, 404 unknown gateway, 503 hermes-hub missing |

Own records come from the `published` table (`local: true`), other gateways' from `replica`; `stale` is true when
the last successful hub pull is older than 180 s. `pending` lists this gateway's unsent/unapplied requests for the
record. Allowed actions: sessions `rename{title}`/`archive`/`unarchive`/`pin`/`unpin`; inbox `act{action, input?}`/
`archive`; tasks `create` (key `<profile>/<board>/new`; `<board>/new` is expanded to `default/<board>/new`)/`comment`/`complete`/`block`/`unblock`/`archive`/`assign`.
The other task actions take the key `<profile>/<board>/<id>`, as the replica publishes it. Any other task key shape
(`<board>/<id>`, a bare id) is refused with 400 before anything is queued; the owner gateway would only reject it as
`invalid_task_route` after the request was sent.

Reads run out of the gateway process. Each GET is answered by one of two `hubview_worker.py` children (niced +5)
over a pipe, and the gateway relays the child's JSON bytes unchanged. Inside the gateway, hubview's pure-Python
decode held the GIL against the asyncio loop that serves the whole API. At load ~100 on 2026-10-30 that made
`/status` (2 ms of CPU) miss the Desktop's 4 s timeout 338 times in 20 min. If a child dies, hangs (60 s) or fails
to spawn, that read is answered in-process and the children stay off for 30 s. A change to `hubview.py` or the
worker on disk replaces the children on their next read. `HUB_SYNC_INPROC=1` keeps every read in-process. Covered
by `tests/test_read_child.py`.

`published` decode caching compares each row's raw JSON across hub database writes, so an unrelated
replica/meta commit does not re-decode all local sessions and task handoffs. Same-version content
changes, additions, deletions and invalid JSON remain visible on the next read. Covered by
`tests/test_published_decode.py`; the replica's separate seq-based cache is unchanged.

## Consumers

Session links qualify the origin by gateway/profile and the supplied lineage IDs.
Omitted `origin_gateway` means this serving gateway; no origin profile is inferred.
Explicitly foreign origins are excluded. Missing historical origin or owner provenance
is returned as `status: unknown, profile: null, ownership_state: unknown`, never default.
Qualified task keys are `<profile>/<board>/<id>`; historical `<board>/<id>` keys stay unknown.
Unqualified legacy requests remain discovery-only. Replica rows are not authoritative release evidence.

- Fork core (`core/hub-sync-replica.md`): owner lookup via `/where`, the Kanban board's fallback to `/tasks` for an
  unreachable gateway, and offline-marked cached rows in the normal sidebar (`/sessions` + `/request`).
- `desktop-plugins/inbox`: other gateways' open cards via `/inbox`; resolve/archive via `/request`.
- `desktop-plugins/session-tasks`: linked cards on other gateways via `/session-links`.
- `desktop-plugins/blocked-panel`: replica session links discover remote/offline cards;
  live owner details classify actionable requests and all mutations stay on that owner.

`/session-links` preserves `block_kind` and `human_request` when present in the
replica, without inferring evidence from prose. **This plugin is not the replica
producer.** The external hermes-hub client's `collect_tasks` and `stage` own the
serialization into `published` and `outbox`. Client 1.4.1 includes and JSON-decodes canonical `human_request` task evidence,
including explicit null on retirement; malformed/non-object JSON becomes null and
legacy schemas omit the field. No event-based reconstruction restores old evidence.
Deploy that client alongside this projection; the projection alone cannot repair an
older producer's omission.

`test_human_request_path.py` calls the actual source producer in a sibling isolated
`hub/` checkout (override with `HERMES_HUB_CLIENT_REAL`) against a throwaway Kanban
database, then verifies its staged JSON from `published` and `outbox`. These are
required passing assertions, not expected failures. No installed/live source,
sync or network request is used.

## Config

- `HERMES_HUB_STATE` — state dir holding `hub.db` (default `~/.local/state/hermes-hub`).
- `HERMES_HUB_CLIENT` — the hermes-hub client module (default `~/.local/share/hermes-mail/hermes_hub.py`; used for `queue_request` / `send_queued`).

## Tests

```
cd ~/code/hermes-mods
uv run --with pytest --with fastapi --with httpx python -m pytest -q -p no:cacheprovider plugins/hub-sync/tests
```

`tests/fake_hermes_hub.py` builds a throwaway `hub.db`; `test_real_client.py` exercises the installed client when
present and skips otherwise.
