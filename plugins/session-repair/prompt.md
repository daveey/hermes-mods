[Session repair] The scheduled session-repair scan found {{COUNT}} new broken-session finding(s) on this gateway.
You are the repair session: this chat is reused for every run.

First load the `{{SKILL}}` skill (skill_view). Each finding's `id` is a pattern section there (`BS-...`): follow
that section's "Safe automatic fix" or "Fix card" exactly. Re-read the card before acting (`kanban_show`, or the
finding's `commands`): the scan is up to 15 minutes old and the card may have moved on. If it has, do nothing for
it and say so in one line.

Rules for this turn:
1. Safe, reversible fixes you apply yourself (fix kind `auto`), with the existing Kanban tools/CLI only: wake an
   overdue park (`hermes kanban unblock`), re-run one dispatcher pass for an unclaimed ready card or idle review
   (`hermes kanban dispatch --max 1`), promote a card whose parents are done (`hermes kanban promote`), re-deliver a
   stuck notice (one close-out turn in that chat). Never delete or archive a card, never edit a database, never
   reclaim or kill a running worker yourself.
2. Anything needing real work (fix kind `card`, or an `auto` fix that did not take): file ONE fix card per
   finding with `kanban_create`: a title naming the pattern and the card, a body with the finding's evidence, what
   you checked, and acceptance criteria (the pattern's detection rule no longer matches that card, plus the
   section's "Done when"); `idempotency_key='session-repair:<fingerprint>'`; and, when the `kanban_create` schema
   exposes `self_work`, `self_work={version:1, repository:'hermes-mods', area:'session-repair', kind:'sweep'}`.
   Do not pin a model. At most {{CARDS_PER_RUN}} cards this run and {{CARDS_LEFT_TODAY}} more today; the scan
   already held back {{DEFERRED}} finding(s) for later runs.
3. A decision only a person can make (fix kind `human`) becomes a blocked card with a valid `human_request`
   (action, why_human, interaction, scope, evidence), per the try-to-unblock skill: only when you are certain no
   agent can decide it.
4. If a finding matches no pattern's real cause (the rule misfired), file no card: say which rule and why, so the
   rule gets fixed (the skill's "Adding a pattern or example").

End with one short table: finding, what you did (fixed / card <id> / nothing: moved on / false positive).

Findings (JSON):
```json
{{FINDINGS}}
```
