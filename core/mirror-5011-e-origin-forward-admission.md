# Verified origin forwarding and enrolled destinations

Mirror entries: 4778, 4779, 4781, 4782.

## Behavior

Origin forwarding prefers a verified local chat owner when available, distinguishes a truly missing origin ledger from temporarily unavailable evidence, and checks destination enrollment before freezing a new forwarding decision.

## How it works

Local-owner selection falls back to the first foreign owner only when no local owner is verified. Ledger lstat treats absence as terminal missing binding while unreadable/dangling/open-race evidence remains conservative. New admissions read fresh authenticated /v2/machines after origin and lease authorization and before freezing. Complete active registry membership must include the caller and destination. A durable retry reuses its decision without another inventory read. Malformed wire decoding, including recursion/decode failures, is normalized inside the inventory boundary as retryable unavailability.

## Code

- `hermes_cli/kanban_origin_destination.py`
- `hermes_cli/kanban_origin_forward.py`
- `hermes_cli/kanban_origin_forward_adapter.py`
- `hermes_cli/kanban_origin_forward_decision.py`
- `tools/kanban_tools_placement.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Missing-ledger tests use table-driven missing, dangling, permission, corrupt and race cases. Cross-board creator-chat tests cover owner preference. Destination/adapter/decision and real courier fixtures cover authorization ordering, unknown destinations, unavailable inventories, lease expiry, durable replay and wire failure recovery.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/hermes_cli/test_origin_destination.py`
- `tests/hermes_cli/test_origin_forward_adapter.py`
- `tests/hermes_cli/test_origin_forward_authority.py`
- `tests/hermes_cli/test_origin_forward_decision.py`
- `tests/hermes_cli/test_origin_forward_missing_ledger.py`
- `tests/hermes_cli/test_origin_inventory_wire.py`
- `tests/origin_transport/test_forwarding.py`
- `tests/tools/test_kanban_cross_board_creator_chats.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

Enrollment is neither origin authority nor placement permission or receiver liveness. Synced discovery and worker caches do not authorize destination admission. An unavailable inventory must not become a permanent caller rejection.
