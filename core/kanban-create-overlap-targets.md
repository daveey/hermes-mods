# kanban_create overlap check compares targets, not only title words

Patches: `patches/1400-fix-kanban-create_overlap-compares-targets-not-only-.patch` (`f5a83d2f11`), `patches/1401-fix-kanban-narrow-create_overlap-target-veto-to-repo.patch` (`13205355ab`), `patches/1402-fix-kanban-overlap-veto-ignores-ports-gpu-indices-an.patch` (`29b193a1dc`). Extends [kanban-create-overlap.md](kanban-create-overlap.md); see also [kanban-overlap-url-slug-and-flaky-cards.md](kanban-overlap-url-slug-and-flaky-cards.md).

## Behavior

Word-overlap alone matched cards of the same *kind* that named different targets (an audit found 12 of 16 refusal groups, 108 attempts, false: e.g. one stale-gateway card per machine, a probe leg per host). After 1402, `kanban_create` still refuses a title-matched open card unless one of these admits the new card (final rules, not the 1400 first cut):

- **Different repo**: both cards name a single `owner/repo` (owners `daveey`, `metta-ai`, `nousresearch`, `softmax-ai`), the sets are disjoint, and at least one *title* names it. A body-only repo counts only if it is the sole repo and appears at least twice (1402); one "see owner/repo" aside does not.
- **Different host**: both titles name hosts and the sets are disjoint. Hosts are whole tokens: aliases (macbook, mbp, mini, hub via `hermes-hub`, ...), gateway names, `metta<N>`/`bb-linux<N>` (`metta-3` = `metta3`, 1402). `gpuN`, `worker-N`, `mini-batch` and device names like `...-mini` are not hosts (1401, 1402).
- **Different process**: both titles name `gateway <n>` or `pid <n>` (3+ digits), disjoint. A port (`gateway 8080 port`, `hermes-gateway:8642`) is not a process (1401, 1402).
- **Inverted check at another revision**: both titles carry different shas and one has an inversion word (removal, revert, drop, disable, rollback...) directly next to a word the other title names, while the other has none (1400 broad, 1401 narrowed). A newer sha alone is never distinct.
- **Waiter**: the new card says it parks/waits/is blocked/depends on `<card id>` (one clause; negated forms such as "do not wait on", "should not be blocked on", "need not stay waiting on" do not count, 1401/1402).

Ticket numbers (`BB-434`, `#123`) were a veto class in 1400 and were removed in 1401: repeat filings of one failing check differ only by ticket number and must stay refused. The same host/process/repo stays refused even when the older card is on another gateway. Admitted look-alikes are logged at INFO with the matching reasons and the admit reason.

Also (1400): a card is reported once per gateway, and under a `HERMES_KANBAN_DB` pin each resolved board file is read once under the board that owns it (previously the one pinned card came back once per board slug).

## How it works

`find()` builds the match list as before (`_reasons`), then for each title-matched card computes the new card's identity once (`_identity`: title targets plus repo from title/body) and asks `waits_on()` and `_distinct()`. A non-empty answer admits (skips) the card and logs it. Local cards and replica cards now carry `body` so a body repo can be compared; bodies are scanned only to 20 KB.

## Code

`hermes_cli/kanban_overlap.py`: `target_identity`, `repos`, `_repo_of`, `_inverts`, `waits_on`, `_identity`, `_distinct`, `distinct_target` (public wrapper), `_board_files`, `_local_cards` (selects `t.body`), `_replica_cards` (`body`), `find`; module docstring updated. Regexes: `_HOST_ALIASES`, `_HOST_PATTERN`, `_PROCESS`, `_REPO`, `_INVERTS`, `_WAIT`, `_NEGATED`.

## Tests

Source tests in `tests/tools/test_kanban_create_overlap_url_slug.py`, not rerun in this mirror (counts below are from the commit messages: 1400 21 new fail on base then pass; 1401 19 new, 81 pass; 1402 6 new, 137 pass across overlap suites).

- 1400: `test_stale_gateway_card_for_another_host_is_not_a_duplicate` (parametrized pairs, admitted), `..._same_process_is_still_refused_even_when_remote`, `test_probe_leg_for_another_host_is_admitted` / `..._repeated_for_the_same_host_is_refused`, `test_mini_disk_incident_is_not_the_metta3_incident` / `test_same_disk_incident_is_still_refused`, `test_probe_with_the_opposite_acceptance_at_a_new_revision_is_admitted`, `test_same_check_is_refused_at_the_same_or_a_newer_revision`, producer call-site, PR181, v12 round and drain-owner true-duplicate controls, `test_red_ci_for_another_repo_is_admitted` / `..._same_repo_is_still_refused` / `..._refusal_does_not_co_match_another_repos_card`, `test_waiter_that_parks_on_its_producer_is_not_its_duplicate` / `test_second_producer_is_still_refused`, `test_pinned_board_db_is_read_once_under_its_own_board_name`, `test_distinct_target_classes` (initial rows).
- 1401: more `test_distinct_target_classes` rows (repeat ticket filing, `#181`, mini-batch, device `-mini`, worker-N, gateway port, drop-flaky-test, disable-old-flag stay refused; inverted OWNER_ASK_KEYS pair distinct); `test_repos_named_only_in_both_bodies_do_not_make_a_duplicate_distinct`; `test_waits_on_needs_an_unnegated_wait_on_that_card` (rows for park/blocked/negated/parent/producer text).
- 1402: rows for `:8642` vs `:8643`, `metta3` vs `metta-3`, `gpu0` vs `gpu2`; `test_a_single_passing_repo_mention_in_the_other_body_does_not_make_a_duplicate_distinct`; two more negated `waits_on` rows.
- Untested: the INFO "admitted" log line; the `_replica_cards` `body` field end to end (tests call `find` on local boards plus `distinct_target`); the 20 KB body cap; the "once per gateway" dedupe as its own case (only the pinned-DB test touches it). The commit messages' live-replica replays are not reproducible from the patches.

## Applying

Patches 1400, 1401, 1402, in that order, `git am -3 --empty=keep`. They need `hermes_cli/kanban_overlap.py` and `tests/tools/test_kanban_create_overlap_url_slug.py` from earlier patches (0865, 0877, 1024; apply 1371 and 1039 first if present, as they touch the same module). 1401 and 1402 rewrite 1400's code, so do not apply them alone. No config; `kanban.create_overlap_check: false` still disables everything.

## Gotchas

- Commit messages cite PR-branch shas (`9f7b233331`, `e6ac0831dd`); the cited SHAs above are the patch headers.
- Host and alias tables are specific to the author's machines (`_HOST_ALIASES`); other deployments get only the gateway-name and `metta`/`bb-linux` rules, so machine vetoes may not fire for them. Repo owners are likewise a fixed list.
- Both sides must name a target in a class; a card that names none is left to word overlap and may still be refused.
- Bare "hub" is deliberately not a host; `hermes-hub` is.
