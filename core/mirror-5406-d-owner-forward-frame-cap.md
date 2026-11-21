# Owner forward: allow large session.resume answers

Mirror snapshot 5406; ordinals 5394.

Entries:

- **5394**: fix(owner-forward): a long chat's session.resume answer no longer refuses every Desktop send

## Behavior

- `connect_owner`'s `max_size` now defaults to `None` (uncapped; was 1 MiB) because the owner is this host's own loopback gateway, so busy chats with long transcripts can still receive forwarded prompts.

## How it works

- `tui_gateway/owner_forward.py`.

## Code

Shipped non-test paths (union over these entries):

- `tui_gateway/owner_forward.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/tui_gateway/test_owner_forward_large_resume.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- The default is now uncapped; callers that want a bound must pass `max_size`.

## Why core instead of a plugin

Forwarding transport is gateway core.
