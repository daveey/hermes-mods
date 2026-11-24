# Dormant explicit-home acquisition: resolver inventory

Scope: `CoreReaders(home_context=PanelHomeContext(...), installation=...)` and its producer. This is an acquisition prerequisite, not full panel parity or an activation gate. The ordinary no-context path is unchanged.

## Bound reads (core and companion plugins)

| Entry / reachable reader | Bound input or refusal |
|---|---|
| `CoreReaders.__init__`, `state_db`, `lineage` | Immutable context's home/state DB; state memo cache identity includes explicit home. |
| `explicit_home.parse_home_sync_config`, `identity`, `read_install_id` | Only supplied home's config/install file or supplied installation mapping. Missing optional config disables optional source; unreadable/malformed config or malformed supplied identity raises. No process-home config loader or active-profile discovery. |
| `boards`, `board_paths`, `_shadow_path`, `conn`, stamps, directory/file watches | Paths rooted in explicit home, config board selection applied, resolved selected DBs outside that home refused. SQLite opens use supplied paths. Legacy `board_paths()` discovery is not entered. |
| `snapshot_path`, `timing_path` | Explicit home's cache, explicit namespace and home-derived profile label; no process launch profile as data identity. |
| `OriginLedger.project` / `explicit_home.origin_rows` | Explicit ledger path, `replica=None`, gateway/profile and home; no default shadow replica discovery. |
| `observe_local_binding` | Explicit home/profile/validated board and exact owner marker; resolved path must remain inside home. Legacy ownership lookups occur only when home is omitted. |
| `replica_cards` | Explicit `replicas` + `profile` bind mode. Omitted origin rows mean `[]`; explicitly empty origin rows remain empty, including unreadable-replica fallback and per-profile index. Constant freshness default or supplied home policy; no ambient sync config. Legacy omitted-scope callers retain prior semantics. |
| `_ledger_fallback`, `_ledger_index` | Supplied origin rows, including empty rows; no cached foreign index or `_remote_links` fallback when rows supplied. |
| `add_cards`, `_read_session_tasks` | Caller gateway/profile and board connection; existing legacy resolver branches remain only for omitted inputs. |
| `_session_tasks`, `linked`, `done`, external capture/inventory | Explicit board connections, scoped origin and replicas, explicit identity/freshness. Transaction/retention-only helpers consume those connections. |
| `card` / `_detail` | Supplied read-only DB; waits explicitly suppress implicit parent-replica discovery. Wake history, events, task/run/dependency/actionability reads use that connection. Legacy queue/capacity acquisition is **not** invoked: `queue_unavailable` explicitly reports the missing adapter. This is not queue parity. |
| `user_step_requests`, `decision_inputs`, `panel_requests_many` | Bound nonempty profile and validated install identity; supplied `ActionStore(path=..., read_only=True)`. `Presentation` invokes captured binding/inbox/step views only, never `Mirror.__init__` (which discovers ambient origin/runtime stores). No missing-identity fallback. |
| `panel_reconcile`, `panel_requests(reconcile=True)` | Bound reconciliation refuses before any ambient Mirror; CoreReaders records explicit sessions as unreached, never successful empty reconciliation. |
| `panel_inputs` / `inputs.acquire` | Explicit root/user-step file/identity and canonical bound action reads. Canonical-read refusal propagates rather than falling back to a raw action store. Subagent/process/rental inventory lacks an explicit adapter: named `input_errors` expose all three rather than silently returning certified-empty inventory. |
| Snapshot capture / board reducers | Compose above scoped readers; no additional process-home source discovery. |
| Hub DB/mail config | Existing typed `UnsupportedHomeAdapter` refusal. |

Pure rendering (`Presentation`), binding reducers, SQLite row descriptions and actionability reductions do not discover stores. Runtime/queue/reconciliation adapters above are remaining parity work, not implicit permission to consult ambient state or activate the dormant transport. Browser payload home/profile fields cannot replace the server-trusted context.

## Evidence to reproduce

- Core reviewer file `tests/hermes_cli/test_review_4255_scope.py` is retained verbatim; fails both probes on the original core head.
- Mods reviewer file `plugins/session-panel/tests/test_review_explicit_populated.py` is retained verbatim.
- `test_explicit_home_sentinel.py` composes both repos with a non-directory process home and tripwires, populated explicit board/action stores, explicit empty replicas and gateway/profile card observations. It also tests invalid config, identity refusal and outside-home SQLite refusal.
- Core `test_replica_explicit_acquisition.py` covers freshness, cold fallback and cached-index isolation; `test_origin_explicit_home.py` covers local binding reads without process ownership discovery.
- Run tests through the core `scripts/run_tests.sh --file-retries 0` with the paired core checkout, plus both complete plugin suites. No environment mutation is used to make production readers target another home.
