# Pitfalls

Reference for skill `hermes-coach` (moved verbatim from SKILL.md).

- Exercise the coach worker's startup and request routing separately. An inherited class-selector model must not be pinned to the worker's historical Anthropic default before the judge model is known. Keep explicit selector/provider conflicts visible. Price the resolved concrete route and freeze its model/provider in every runner (including transcript/dictionary judge paths) before reserving budget; otherwise another pooled worker can select a different route after pricing. Run `scripts/test-all -k hermes-coach` from a complete checkout: copying only scripts loses sibling-plugin fixtures and Git-based repository guards. Require a budget-reserved real judge response before calling a startup repair complete; all-None results are INCOMPLETE.

- Before weekly provenance/legacy withdrawal changes, read `references/weekly-provenance.md`: source identity,
  ambiguous raw evidence, and cold file-path consumer tests. Local import-order success never waives red CI.

- xAI `x_search` via the Responses API (outside research scout): send `"tool_choice": "required"` and use
  `grok-4.5`, or the model often answers `[]` from memory without searching (`usage.num_server_side_tools_used: 0`).
  Each call costs ~$0.4–0.7 (`usage.cost_in_usd_ticks` / 1e10 = USD), so log it and cap per day. Citations come back
  empty; the post URLs are in the answer text. Check them with `https://publish.twitter.com/oembed?url=<post>`
  (in testing 21/21 resolved).

- Before recommending a harness change from outside material (a tweet, an article, a scout idea), grep for an existing
  mechanism first: coach detectors (`plugins/coach/fix_loop.py`, `reply_check.py`), core `VALID_HOOKS` in
  `hermes_cli/plugins.py` (e.g. `pre_verify`), `config.yaml` (`delegation`, `auxiliary`), and the target app's own
  code. Write the proposal as a delta on what exists ("make the hint act"), never as a new build, because a
  config-only skim proposes duplicates of features that already ship.
- Verify redaction before the first push to a new repo: `gitleaks dir <repo>/<host>` must report no leaks.
- Hermes writes many user-role messages itself: delegation results, cross-session messages, turn-cap notices,
  `/goal` continuations, `[IMPORTANT: …]` notices, compression summaries, model-switch notes. Counted as user turns,
  they inflated repeat-fix about 10× and the Hermes-meta share about 4×. Extend `SYNTHETIC` whenever a new
  prefix appears (`grep -rhoE '"text": "\[[A-Z][A-Za-z: ]{4,40}' <host> | sort | uniq -c`), and normalize
  per-session rates by real user turns, not by the kept turn sample.
- Sanity-check a new KPI against the raw numbers before reporting it. Weight delegated work by spend: a count-based
  Kanban share (sessions vs orchestrating chats) reads 68% when the cost-weighted one is 14%, because each card is a
  session while one chat can hold 50 subagents.
- Treat the coach's first report as a check of the pipeline itself: it audits the digests and finds metric bugs.
  Fix those first, before acting on its usage findings.
- In weekly KPI audits, distinguish same-profile operator-note stamps from author provenance: `(another session)` can also label a human CLI comment. Retain ambiguous notes unless the stored writer/run proves automation. Match generated card, probe and JSON envelopes before both classification and the scanned-turn denominator; preserve real opener corrections. Replay the exact previous reply, including a leading conditional clause, when adding ask shapes. Keep descriptive UI flows and negated requests as negative controls.
- Verify clipping fixes through `digest_db` against the visible stored row, not only a cleaned audit excerpt. Context-reference expansion appends `--- Attached Context ---` or `--- Context Warnings ---` after the user's text; strip those producer-defined footers before head/tail clipping, or an external page displaces the correction. Keep full-row and plain-text controls in regressions.
- When reconstructing delayed digest intake, use the first commit containing the exact turn, not the commit that first created its session file. Compare that arrival with the consumer checkout's reflog and the backstop lookback; upstream author time alone is not proof the consumer saw it.
- Keyword signals (`hermes_meta` etc.) hit incidental mentions. Classify a session by title keywords or by the
  signal's share of real turns, never by a raw count ≥ N.
- A new curriculum `adopted` test that needs a fact the digest lacks: add a counter to `call_features()` /
  `CMD_FEATURES` in `session_digest.py` (names only, never argument text), bump `DIGEST_V` (that also invalidates every host's digest cursor, so the next feed re-reads all sessions), re-ship, rerun every feed.
- Feed slow or timing out: read `elapsed_s` (per phase) in the coach_feed status line and `skipped`/`elapsed_s` in its
  `digest`; a no-change digest should skip nearly every session in well under a minute. The hub wrapper warns past 300 s.
  Sanity-check new adoption counts against real sessions before trusting them (agent-run commands count too).
- Reply-check rules match one model's failure modes, so silence after a default-model change means nothing: Opus fails
  by asking ("Want me to…?", the `confirm` rule), GPT-6 Astra by reporting accurately and stopping ("not fixed; nothing
  needs you"), which no rule matched. Re-baseline per model and trigger before trusting the detectors, attributing
  each turn to the model that served it: fallback and mid-chat switches serve some turns on another model, so
  `sessions.model` and the digest's per-session `model` mislabel them. Recipe: the Evaluation section of the
  hermes-training skill's `coach-detectors.md` reference.
- An instruction fix (`~/AGENTS.md`, SOUL.md, a skill) is live only in sessions whose prompt contains it. Core loads
  AGENTS.md from the git-root → cwd chain only, so a home-level rule reached 0% of Kanban workers (scratch cwd), 0%
  of cron and 55% of chats (those opened in a project folder missed it) until the fork's `agent.global_context_files`
  loaded it everywhere. Before reporting a rule change live, count sessions started after it whose stored prompt
  (`state.db` `sessions.system_prompt_hash` → `system_prompts.prompt`) contains a phrase from the new text, per
  source and per host. Delegated child agents load no context files by design; judge the parent.
- Verify a regex detector's hits (e.g. `coach_askgate.py`) against what the agent said just before, not the quote alone,
  because quotes like "why didnt the agent…" or "…yourself" fire on third-party bots and on handoffs nobody asked for.
  Local sessions: the previous assistant row in `state.db` `messages`. Other hosts: the digest
  `~/code/hermes-coach-data/<host>/<profile>/<sid>.json` has `title`, user turns and `last_reply` only, never the reply
  before a mid-session correction. Read that with `cross_session` action=read, target `@session:<profile>@<host>/<sid>`
  (profile from the findings row or the digest path); a bare `<host>:<sid>` reads the default profile, and the same id
  can exist under several profiles. A compacted session returns only its tail; later replies there often show the
  pattern still recurring, which is stronger evidence than the old quote.
- Reviewing a week's ask-gate hits (the Inbox Do-it): per hit, pull the reply before the correction and grep the skills
  for a symptom fix that already landed. Then run the ask-gate plugin's `core.find_asks` over all those replies: the
  ones it finds no ask in are the root gap, fixed in the plugin with the replies as test fixtures (a card behind any
  in-flight ask-gate branch, not an edit to its worktree). For bot-profile hits, read the bot's `SOUL.md` and prerun
  prompt in `~/code/hermes/hosts/<host>/<bot>/` (branch `state`): a report template that demands an asks section, or a
  stale capability claim such as a too-narrow token scope, produces the asks, and cron runs bypass the ask-gate.
  After narrowing a detector, diff the old and new hit lists and explain every dropped hit. Structural rules like
  "skip a session's first turn" drop real hits, because a new session can open by answering an ask from another session.
- Reviewing the weekly `had_to_ask_fix` hits, in order:
  1. Compare the week with the ship dates (`git log --reverse -- plugins/coach/self_heal.py plugins/ask-gate`) and the
     lane mode (`coach/selfheal-config.json`, `coach/log.jsonl` events): hits from before a mechanism shipped, or while
     it was in shadow, are not detector misses. Say so, then judge today's detector by replaying them.
  2. For ask-me hits, look for an earlier review first (`plugins/ask-gate/tests/fixtures/week_<monday>.json`). If its
     tests pass, the root-cause work is already done; don't redo it.
  3. Replay through `self_heal.complaint_verdict` the way the backstop sees them (digest text, `prev`,
     `backstop_context`) and read the per-stage scores. A `[0.9, 0.0]` with a `product_bug` strict stage is the
     product-bug scoping (Baby Bear hits are `had_to_ask_fix_bb` and go to bb-tuner, not the lane). An empty strict
     answer is a judge failure. Run the same variants over ~150 random real turns from that week as a control, and
     read the extra hits before calling them false positives: many random turns are real complaints.
  4. On the hub, run any replay under the cron interpreter (`cron.scheduler_script._posix_cron_script_argv`). A bare
     `python3` lacks ruamel, so `aux_judge` raises and the fail-open detector silently scores every turn "no defect".
     In practice: the project venv python (`pm.environments.project_python(~/.hermes/hermes-agent)`) from a bash
     wrapper with `HERMES_DISABLE_LAZY_INSTALLS=1`, no `PYTHONPATH`, and `sys.path.insert` of hermes-agent in the
     script. Without the env var the first `agent.*` import re-execs onto the store python and loses ruamel. Start every
     replay with one `aux_judge` call that must return text. All-`None` stages mean a broken judge, not misses.
  4b. When stage scores look wrong, print the raw judge answers before tuning prompts. A judge answering in another
     judge's keys means the private overlay (`coach/reply_profile.md`) carries an output-format line. That line is
     stripped on load since 818e453; on another host, check its overlay too. Flash at temp 0 still varies by up to
     0.65 on the same prompt, so compare variants over ≥2 runs.
  4c. Before starting, check `hermes kanban list | grep -i ask-gate`: the weekly job also opens a review card for
     the same hits. Split the files with it via a card comment, so two runners don't edit one working tree.
  5. Long replays (>5 min) go to a background terminal with notify. A foreground call past the tool timeout dies
     without output.
- Changing bb-tuner's pre-run or SOUL (it runs on the hub, profile `bb-tuner`): the source is
  `hosts/hermes-hub/bb-tuner/` in daveey/hermes@state. The hub's `repos/hermes-state` clone is sparse to skills only,
  so make a scratch `--filter=blob:none` sparse clone of that path. Auth it with the bb-tuner `.env` `GITHUB_TOKEN`
  as `-c http.extraHeader="Authorization: Basic <b64 x-access-token:TOK>"` (the same as `scripts/skills_sync.sh`).
  Diff it against the live `profiles/bb-tuner/{SOUL.md,scripts/}` first, add a `tests/` case, then commit, push, and
  copy the files into the live profile.
- Exclude shared team-bot profiles with a glob (`coach_feed --exclude-profile 'alpha*'`): they hold other people's
  conversations, and a Slack follower runs as `source=cli`, so each teammate message counts as a user turn and its
  frustration/stuck/repeat_fix signals inflate the host's KPIs. An exact name (`alpha`) missed the sibling
  `alpha-follow` profile. Defense in depth: list `HOST/PROFILE_GLOB` lines in the data repo's `exclude-profiles.txt`
  (or `coach_mine --exclude-profile`); `coach_mine.load()` drops those digests from KPIs and the labeling queue.
- `[REDACTED]` inside ordinary prose usually means the `token|password …` rule caught a word. That rule requires
  a digit and ≥12 chars to keep false positives down.
- User messages can be JSON lists of multimodal parts. `flatten()` keeps the text and turns images into `[screenshot]`.
