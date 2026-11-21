# Chat start: build-time quota walls show a route-refused card

Mirror snapshot 5406; ordinals 5326, 5327, 5362, 5363, 5364.

Entries:

- **5326**: fix(tui): a pinned route's quota wall hit at agent build shows its reset and Choose a model
- **5327**: fix(tui): review fixes for the build-time quota wall card
- **5362**: fix(tui): an all-walled fallback chain at agent build names the pin and the earliest reset
- **5363**: fix(tui): review follow-ups for the all-walled chain card
- **5364**: fix(tui): last review nits on the all-walled chain card

## Behavior

- A chat pinned to a quota-walled subscription route used to fail at agent *build* time with a generic "couldn't start this chat" card, a raw epoch reset in a toast and setup advice. It now yields the same `route_refused` card as the pre-build refusal: a human-readable reset time and a pointer to `/model`.
- **5326**: `walled_pin_reply` is factored out of `held_reply` (text unchanged); `quota_refusal_wall(exc)` returns `(provider, model, resets_at)` when a terminal quota refusal names its route with a finite absolute reset; the build-failure handler records `failure_route`, which client frames and RPC errors never carry.
- **5327**: review fixes. Only a single route's own refusal qualifies (a fallback chain's mixed cause does not, and no fallback is called "pinned"); a new build clears an earlier build's recorded route; `route_refused` turn errors read as their own sentence.
- **5362–5364**: the same card for an all-walled chain: a rate-limited pin whose fallbacks are all typed quota refusals with finite resets. `quota_refusal_walls` returns walls pin-first; repeated routes fold into the later reset; chains nested in chains, past-reset pins/fallbacks and malformed records stay generic; the chain's earliest reset is the client `reset_at` and the replay clock.

## How it works

- `agent/model_routing_unavailable.py` classifies refusals; `agent/model_routing_walls.py` renders wording.
- `tui_gateway/session_registry.py` records the route on build failure, `tui_gateway/methods_prompt.py` closes the turn with a `route_refused` descriptor, `tui_gateway/user_messages.py` and `credential_failure.py` shape the user-facing text and strip internal fields from client frames.
- A desktop renderer test pins the completion card.

## Code

Shipped non-test paths (union over these entries):

- `agent/model_routing_unavailable.py`
- `agent/model_routing_walls.py`
- `tui_gateway/credential_failure.py`
- `tui_gateway/methods_prompt.py`
- `tui_gateway/session_registry.py`
- `tui_gateway/user_messages.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `apps/desktop/src/app/session/hooks/use-message-stream/route-refused-completion.test.tsx`
- `tests/tui_gateway/test_agent_init_quota_wall_card.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Anything ambiguous (mixed causes, nested chains, already-passed resets, unreadable wait) deliberately falls back to the generic card.
- Reset times are rendered in the viewer's configured zone convention used by the existing refusal card; this entry does not add configuration.

## Why core instead of a plugin

Build-time failure handling, route classification and the turn-closing descriptor are gateway core; the desktop plugin surface only renders what core emits.
