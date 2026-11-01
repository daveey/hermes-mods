# kanban_create overlap check compares targets, not only title words

Patches: `patches/1400-fix-kanban-create_overlap-compares-targets-not-only-.patch` (`f5a83d2f11`), `patches/1401-fix-kanban-narrow-create_overlap-target-veto-to-repo.patch` (`13205355ab`), `patches/1402-fix-kanban-overlap-veto-ignores-ports-gpu-indices-an.patch` (`29b193a1dc`), `patches/1506-fix-kanban-create_overlap-tells-work-role-and-failed.patch` (`bbf69bee23`, work role and failed-card recovery, last section). Extends [kanban-create-overlap.md](kanban-create-overlap.md); see also [kanban-overlap-url-slug-and-flaky-cards.md](kanban-overlap-url-slug-and-flaky-cards.md).

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

## Follow-up patch (1506): work role and failed-card recovery

**1506** (`bbf69bee23`, `fix(kanban): create_overlap tells work role and failed-card recovery apart`).

- **Behavior.** Two more ways a title-matched open card is admitted (not refused):
  - **Different work role** (`_distinct`, also through `distinct_target`). Each card gets at most one role: `implement` (fix, repair, implement, design, redesign…), `measure` (measure, re-measure…) or `deliver` (follow-through, send, deliver, notify, relay…). A title's role comes from the first word of a clause, skipping words with digits such as `7-day` or `BB-1156:`, so a noun like "send" in "Discord send retries drop" does not count. A negated word ("not a second verification campaign") does not count, but negation never reaches across a clause. Hyphenated tokens are one word (`failed-fix`, `bb-self-fix` are not "fix"). If the title names no role, only `implement` is read from the first 600 characters of the body ("smallest fix" in a bridge-filed body). Two cards are admitted when their roles differ, at least one role comes from a title, and neither title uses the other card's role words. So "Send button disabled" vs "Fix send button disabled" stays one defect. A title naming two roles has none, and an ambiguous title never falls back to its body. Verifying is not a role: "Verify green main CI" still owns "Fix red main CI".
  - **Recovery of a failed card** (`recovers`). A title of the form `card t_X [on <gw/board>] failed|crashed|stalled|timed out|gave up|got stuck|is stuck|lost its worker` is admitted against t_X itself. This is the self-heal lane's `self-heal: kanban card t_X on mini/bb failed 2x (...): <t_X title>`, which was refused 8 times because it repeats t_X's board and title. Text after the next colon that quotes t_X's title is ignored for that test. The exception does not apply if the title says it redoes the work (`take over`, `redo`, `retry`, `rerun`, `resume`, `continue`, `finish`, `refile`, `replace`, `instead of`…). A second recovery card for the same source is still refused, because recovery only excuses the source card.
- **Incidents replayed** (from `kanban_overlap_role_cases.json`; in this public mirror the cases are synthetic equivalents of the original create inputs, see Gotchas): a Stage B "repair evidence gaps" card refused against the Stage C verifier and a report-sending follow-through (tool path); the self-heal card above (CLI path, 8 refusals); a "7-day re-measure" card refused against four cluster fix/design cards on the same board (tool path).
- **Code.** `hermes_cli/kanban_overlap.py`: `_ROLES`, `_role_tokens`, `_one_role`, `work_role`, `_RECOVERS`, `_REDOES`, `recovers`, `_identity` (adds `role`, `role_src`), `_distinct` (role rule), `_role_words_in`, `find` (admits `recovers <id>` after `waits on`); the module docstring lists both.
- **Tests** (new `tests/tools/test_kanban_create_overlap_roles.py` plus `tests/tools/kanban_overlap_role_cases.json`). With the public patch applied at fork tip `37c70bbcaa1c`: 48 passed in that file, and 208 passed together with `test_kanban_create_overlap.py`, `test_kanban_create_overlap_url_slug.py`, `tests/ci/test_flaky_kanban_overlap.py` and `test_kanban_capability_routing.py`. Against the pre-1506 `kanban_overlap.py` the same 31 tests fail with the synthetic fixture as with the original one, so the replays still catch the bug:
  - `test_stage_b_evidence_repair_is_not_owned_by_the_verifier_or_the_sender`, `test_stage_b_same_role_cards_in_the_campaign_are_still_refused`, `test_original_stage_b_duplicate_campaign_is_still_refused`;
  - `test_selfheal_recovery_card_is_not_refused_by_its_source_card` (CLI `hermes kanban create` argv, minus `--skill`) and `..._via_the_tool`, `test_a_second_recovery_card_for_the_same_source_is_still_refused`, `test_a_card_redoing_the_source_cards_work_is_still_refused`;
  - `test_remeasure_is_not_owned_by_the_cluster_fix_and_design_cards`, `test_a_second_remeasure_and_a_same_cluster_repair_are_still_refused`, `test_audited_true_duplicates_are_still_refused` (parametrized);
  - unit tables `test_work_role`, `test_a_body_only_role_never_decides_against_another_body_only_role`, `test_role_words_that_are_not_the_job_never_split_a_duplicate`, `test_fix_red_main_ci_is_still_refused_by_a_verify_card_whose_body_sends_a_link`, `test_recovers`.
  - The REST `POST /tasks` entry is not exercised for the new rules.
- **Gotchas.** The role words are a short, fixed English list. A card whose title starts with another verb ("Investigate", "Audit") has no role and falls back to the existing word and target rules.
- **Public fixture is synthetic.** The fork commit replays the refusals from the original card and session text. This mirror's `kanban_overlap_role_cases.json` holds short synthetic equivalents instead, not the original text. Its `_source._note` says so. The synthetic cases keep the titles where they hold nothing private and every feature the check reads: board references (`research-collab`, the `bb` board, `mini/bb`), repo slugs (`daveey/research-collab`, `daveey/myapp`), the body's opening role word, and task references. Task ids and the self-heal fingerprint are synthetic. The test module and the 1506 comments in `kanban_overlap.py` drop audit ids, timestamps and quoted user text; the code is unchanged (same AST). So the patch's three post-image blobs differ from the fork's. `verify_mirror.py` needs a reviewed fingerprint pair for each of the three paths.
