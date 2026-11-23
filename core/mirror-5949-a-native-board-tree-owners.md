# In-process board and project-tree fact owners

Patch coverage: **5932**, **5933**.

## Behavior

The bundled Kanban board (board, shadow and catalog views) and the native project tree are served from canonical facts captured off-request by an in-process owner, instead of hydrating source state per request. A cold, missing, foreign-scope or failed capture returns retryable unavailability (HTTP 503, `facts_unavailable`, `Retry-After: 1`), never a legacy fallback. `HERMES_FACTS_BOARD` is read once at startup and defaults on; off restores the legacy board lifetime once and leaves tree activation absent. Current-board selection uses the captured catalog. Authorized board and profile rosters are fixed at startup; new scopes fail closed until a later authorized boot. 5933 pins that a provider rejects a home outside the authenticated one, unknown boards or profiles, and a closed owner.

## How it works

`fact_view_owner.ViewOwner` owns scoped views on the existing ContentOwner feed. `fact_board_inputs` provides one bounded round-robin evidence source per scope; `fact_board_native` captures detached inputs on the auxiliary worker and projects them with canonical serializers only. `fact_board_startup` builds a `BoardRuntime`, and `fact_plugin_mount.PluginLifecycles.start` calls it when the module is the trusted bundled board plugin (other plugins keep `start_legacy`/`bind_facts`). `fact_tree_startup` supplies `TreeRuntime`, entered from `app_lifespan` after links and sessions and before the plugin mount, preserving lifetime order. `tui_gateway/project_tree_capture` records the canonical builder's own probe closure so rendering replays evidence without guessing absence; `methods_projects` splits legacy and capture builders over one input path. A failed shutdown blocks admission while keeping retryable custody; healthy siblings still drain.

## Code

- `hermes_cli/fact_view_owner.py`, `fact_board_inputs.py`, `fact_board_native.py`, `fact_board_startup.py`, `fact_tree_inputs.py`, `fact_tree_startup.py`, `fact_plugin_mount.py`, `web_server_app.py`.
- `plugins/kanban/dashboard/board_fact_capture.py`, `board_shadow.py`, `plugin_api.py`: capture hooks and import changes.
- `tui_gateway/methods_projects.py`, `tui_gateway/project_tree_capture.py`.

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live activation, boot, mutation, rollback or performance claim is made.

- `tests/hermes_cli/test_fact_board_inputs.py`, `test_fact_view_owner.py`, `test_fact_tree_startup.py` (including, from 5933, provider binding to the authenticated home plus escape and closed-owner rejection).
- `tests/plugins/test_board_native_inputs.py`, `test_board_native_owner.py`, `test_board_shadow_capture.py`, `test_board_startup.py` (5933: foreign home, unknown board, closed owner), `test_board_teardown.py`.
- `tests/tui_gateway/test_project_tree_capture.py`, `test_project_tree_native_capture.py`.
- `tests/gateway/test_native_http_auth.py`: the project-tree route may answer 503 `facts_unavailable` until the scoped selector is captured, and a redeemed grant is never replayed.

Source tests exist for the plugin-mount and app-lifespan entry paths. Untested here: a real server boot with the flag on and off, rollback, and mutation visibility timing (local hints plus a bounded recapture interval do not imply immediate upstream truth).

## Applying

Apply the covered patches only as part of the full ordered core series, following the whole segmented recipe in the [core README Applying section](README.md#applying) (replay bridges and required repair overlays included). The mirror is append-only and these entries depend on earlier ones; do not run a standalone `git am` on them and do not treat them as an independent cherry-pick set. A reimplementation on a newer upstream should keep the invariants above and run the named suites in a disposable source checkout.

## Gotchas

- Never call capture functions from a request; they run on the owner worker.
- Roster changes need a restart; new scopes are unavailable until then.
- Keep native host adapters outside the plugin boundary; the trusted mount passes existing serializers explicitly.
- Tree activation is independent of sessions sidebar ownership.
- Requires the earlier fact-store, content-owner and links-startup entries in the series.

## Why not a plugin

No: it runs at the trusted bundled-plugin mount and app lifespan, owns scope and source custody for the plugin, and must exist before plugin code is served. A plugin cannot own its own lifetime or authorization scope.
