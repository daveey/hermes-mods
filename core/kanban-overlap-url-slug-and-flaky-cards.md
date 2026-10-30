# create_overlap ignores URL slugs; ci-flaky-cards owner rules

Patches:

- `patches/1000-fix-ci-ci-flaky-cards-defers-to-the-card-core-s-crea.patch`
- `patches/1009-fix-kanban-a-slug-in-a-URL-or-path-is-not-a-board-re.patch`
- `patches/1010-fix-ci-ci-flaky-cards-accepts-an-overlap-match-as-ow.patch`
- `patches/1011-fix-kanban-create_overlap-log-line-names-the-matched.patch`
- `patches/1012-fix-ci-ci-flaky-cards-takes-an-overlap-match-as-owne.patch`
- `patches/1014-fix-ci-ci-flaky-cards-board-scan-counts-a-card-as-ci.patch`

`create_overlap` refused seven unrelated creates because `/hermes` inside a URL or path counted as a board reference, and `ci-flaky-cards` then deferred to whatever card the guard named, losing three live flakes.

## Behavior

- 1009: a plain-word slug is referenced only by `board <slug>`, `--board <slug>`, `<slug> board`, `@task:<slug>@gw/...` or `<gateway>/<slug>` as a whole token; `/<slug>` in a URL, path or slash list no longer counts. Naming a plain-word board needs 2 shared title words that are at least 25% of the longer title. 1011: the refusal log line names up to 8 matches (gateway/board/id and reason).
- 1000/1010/1012/1014 (`scripts/ci/flaky_kanban.py`): a match owns a flake only if it is open (status re-read from its owner; canonical replica rows win) and its title or lead paragraph names this test and no other, or its key is `deflake:<id>`. Non-owners go back as `--distinct-from` with a reason. A remote owner gets the comment through hermes-hub request with a deterministic request id and it is read back; an unconfirmed delivery logs FAILED and leaves the run unprocessed. The board scan uses the same rule.

## How it works

`kanban_overlap.py` tokenizes references; `flaky_kanban._is_about` is the shared ownership predicate.

## Code

`hermes_cli/kanban_overlap.py`, `scripts/ci/flaky_kanban.py`.

## Tests

`tests/tools/test_kanban_create_overlap.py`, `test_kanban_create_overlap_url_slug.py`, `tests/ci/test_flaky_kanban.py`, `test_flaky_kanban_overlap.py`.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

Extends the existing [kanban-create-overlap.md](kanban-create-overlap.md).
