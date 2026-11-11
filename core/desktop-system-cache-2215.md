# Keep Desktop's full-system cache layout stable across rounds

Patches **2209–2210**; 2210 supersedes 2209's round-dependent allocation.

## Behavior

Attended Desktop sessions use one system-side cache marker at the end of the full shared system prompt in the direct-native tool-cache layout. Tools retain their marker and up to two completed transaction endpoints retain rolling markers, within the four-marker budget. The layout stays stable as tool rounds accumulate instead of spending a spare slot on the system suffix only in early requests.

Machine-paced and non-Desktop callers retain the static-prefix fallback. This does not promise a provider cache hit, a fixed token saving or a latency bound; shared-prefix bytes and provider cache eligibility still matter.

## How it works

Initialization pins `_cache_full_system` from attended status and resolved session source. Both request assembly and provider-failover redecoration pass that choice into `build_prompt_cache_plan`; an unrouted cache-parity review fork inherits it.

For the direct-native tool-cache branch, `cache_full_system=True` marks the system suffix rather than the static-prefix block, or falls back to marking the whole system when it cannot split the prefix. The tools marker and last two completed transaction endpoints remain intact. Copy-on-write planning strips prior decoration and avoids empty text blocks.

Patch 2209 first marked the suffix when fewer than two transaction endpoints existed. Patch 2210 removes that early-round heuristic and its tests, replacing them with the stable attended-Desktop layout. Document the final behavior, not the intermediate heuristic as a separate current feature.

## Code

- `agent/agent_init.py::_init_prompt_cache_config`: pin the session layout.
- `agent/prompt_caching.py`: system prefix/suffix marker controls and cache plan.
- `agent/turn_request_assembly.py` and `agent/conversation_loop.py`: propagate layout through initial assembly and failover.
- `agent/background_review.py::build_cache_parity_fork`: inherit layout for an unrouted fork.

## Tests

Supplied source coverage, **not executed by this documentation/mirror task**:

- `tests/agent/test_desktop_system_cache.py::test_fresh_sessions_share_marked_system_and_rounds_keep_wire_bytes`: equal shared-system plans across distinct requests, stable system bytes across tool rounds, rolling endpoints, budget and idempotence.
- `test_layout_is_pinned_at_init_for_attended_desktop_only`: attended/source combinations, including non-Desktop and machine-paced negatives.
- `test_failover_redecoration_keeps_pinned_full_system_layout` and `test_cache_parity_fork_inherits_pinned_layout`: redecoration and fork propagation.
- `test_desktop_wire_marks_full_system_without_changing_rounds`: real request assembly and SDK serialization against a mock HTTP transport. This verifies wire layout, not live provider cache accounting.
- `test_full_system_layout_has_exactly_one_system_marker`: equal/missing/mismatched prefixes, whitespace suffix and structured content without mutating input.
- `tests/agent/test_prompt_caching.py`: existing static-prefix/tool-cache coverage; 2210 removes 2209's spare-slot-only cases.

## Applying

Use only the complete ordered replay recipe in [README.md](README.md), including bridges and verification. Do not apply these patches independently. No new user setting is introduced; fresh agent initialization chooses the pinned layout.

## Gotchas

Full-system selection does not enable caching on an unsupported or disabled route. The specialized marker change applies to the direct-native tool-cache branch, not every provider envelope. A routed review fork builds its own context rather than inheriting cache parity. Published source measurements are not fresh mirror test results.

## plugin?

No. Cache marker allocation is wired into core request assembly and provider redecoration, not a plugin hook.
