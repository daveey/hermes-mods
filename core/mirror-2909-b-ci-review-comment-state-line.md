# CI review comment: footer, writer-race state line, Docker trigger

Patches: 2789.

- **2789** (`ee2af7d54b`, ci(review-comment): answer PR #1382's review points (footer, writer race, Docker trigger))

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- Footer now says the comment is refreshed when CI or Docker completes on the head, and hourly; not live. The `ran on [sha](...)` prefix that ship parses is unchanged.
- Each write puts `<!-- hermes-ci-review-bot:state head=<40hex> started=<UTC us> run=<id> -->` right after the marker. Before writing, a run reads the bot comment and skips (logging why) when it holds a snapshot of the same head from a run that started at or after this one. Another head, or a comment without a state line, is replaced as before.
- The Docker build/test/publish workflow now triggers a targeted refresh by exact name with the same `wt/**` filter; a sibling completion refreshes only once CI is done.

## How it works

- `scripts/ci/batch_review_comments.py` adds state recording/parsing/ordering and the skip check; `ci-review-comment.yml` adds the sibling workflow trigger; the workflows README documents it. A per-head concurrency group cannot do this because one hourly sweep covers every PR.

## Code

- `.github/workflows/README.md`
- `.github/workflows/ci-review-comment.yml`
- `scripts/ci/batch_review_comments.py`

## Tests

- `tests/ci/test_batch_review_comments.py`: `test_footer_names_both_refresh_triggers`, `test_an_older_run_never_overwrites_a_newer_snapshot_of_the_same_head`, `test_a_snapshot_of_another_head_or_without_state_is_replaced_and_the_write_records_its_state`, `test_recorded_state_round_trips_and_orders_runs`, `test_every_watched_sibling_workflow_also_triggers_a_targeted_refresh`, `test_sibling_completion_refreshes_only_once_ci_is_done`, `test_main_passes_the_triggering_workflow`.

Test files touched by these patches:

- `tests/ci/test_batch_review_comments.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Builds on the earlier refresh-on-CI-completion patch (2727). The check-then-write is not atomic; the state line narrows the race rather than closing it.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.
