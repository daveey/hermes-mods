# Kanban placement: published checkout-only deletions do not pin a card

Mirror snapshot 5406; ordinals 5328, 5329, 5330.

Entries:

- **5328**: avoid pooling pins for published checkout-only deletions
- **5329**: Retain remote publication checks for checkout deletions
- **5330**: Require positive remote publication for checkout deletion exception

## Behavior

- A generic dirty verdict used to pin a pooled card to one worker when its owning clone had only deletions (a checkout artifact) while the real published worktree was clean.
- **5328**: recognise only uniform unstaged deletions, or uniform staged deletions with an empty index, on a symbolic branch whose upstream contains HEAD, with no hidden index edits, hardened Git probes and the existing shared deadline. Mixed edits, partial staging, missing upstream, local commits and unknown checks still pin.
- **5329**: a local branch can be an "upstream" without publishing HEAD; the unpushed-commit and remote publication checks are retained after accepting checkout-only dirt.
- **5330**: require a remote-tracking ref containing HEAD before granting the exception (the legacy unpushed predicate allows a local-trunk baseline with no remote refs).

## How it works

- `kanban_checkout_deletions.py` holds the classifier; `kanban_move_pool.py` consults it and keeps the publication checks. Child scanning and removal dirty checks are unchanged.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_checkout_deletions.py`
- `hermes_cli/kanban_move_pool.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/hermes_cli/test_kanban_checkout_deletions.py`
- `tests/hermes_cli/test_review_checkout_local_upstream.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- The exception is placement-only; clean-tree policy elsewhere is unchanged.
- Without a positive remote-tracking ref the card still pins, even if the branch looks contained locally.

## Why core instead of a plugin

Pool placement and pin decisions are made in the kanban move code inside core.
