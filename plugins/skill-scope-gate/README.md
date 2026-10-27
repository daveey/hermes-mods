# skill-scope-gate

Automatic skill edits can't turn one task's caution into a blanket rule.

The background self-improvement review (and the curator walk) patch shared skills on their own after a turn. Every
session on every host then loads that text. A caution that fit one card ("this run is diagnosis-only, don't restart the
runner") becomes an unconditional "don't act" rule when the review writes it without its condition. Example: the
hub's review added a step 0 to five-whys ("do not start services or builds, restart ... without authorization"), and
because `AGENTS.md` sends every failure analysis through five-whys, every analysis read as "diagnose, don't fix".

## Behavior

A `pre_tool_call` hook on `skill_manage`. For an edit whose skill-ledger actor is `curator` (the background-review
fork or the curator walk), each sentence the edit ADDS is classified (`gate.py`):

- authorization requirement: "without authorization/approval/permission", "wait for approval", "get it confirmed",
  "ask David first";
- mode narrowing: "diagnosis-only", "investigation-only", "read-only scope/lane/work", "report-only",
  "report ... instead", "instead of fixing";
- prohibition on acting: clause-initial "do not / don't / never / must not / avoid" + an action verb (start, restart,
  fix, deploy, merge, push, run, build, delete, cancel, change, ...).

A hit passes when the same sentence names its scope: `when / if / unless / while / during / until / once / where /
except`, "for/in/on/during <...> card/task/run/investigation/host/...", a leading context phrase ("In the user's real
window, ..."), or a purpose clause for anti-workaround rules ("merely to evade the gate", "to dodge the quota"). A
condition that is itself an authorization ("unless David approves", "until the user confirms", "if not authorized")
is not a scope. Prohibitions on a code span (``never `git commit -a` ``) are technical pitfalls and pass. Quoted text
and fenced code are ignored. Sentences already in the file (only the patch's `old_string`, or the current file for
`edit`/`write_file`) don't count.

An unscoped hit blocks the edit; the tool result tells the reviewer to name the condition in the same sentence or drop
it. Foreground edits (a user-directed session) are never blocked; their hits are logged as `shadow`.

Telemetry: every gated-path `skill_manage` write appends one line to `<HERMES_HOME>/logs/skill-scope-gate.jsonl`
(`verdict` pass / block / shadow, actor, skills, hits) and logs `skill-scope-gate <verdict> actor=... skills=...`.

`SKILL_SCOPE_GATE=off|shadow|on` (default `on`); `SKILL_SCOPE_GATE_LOG` overrides the log path.

## Dry run over past edits

    python3 plugins/skill-scope-gate/dryrun.py ~/.hermes/repos/hermes-mods ~/.hermes/repos/hermes-state --since "14 days ago"

Classifies the sentences each `--author` (default `hermes-hub`) commit added to skill markdown.

## Tests

    uv run --with pytest python -m pytest -q -p no:cacheprovider plugins/skill-scope-gate/tests

`SKILL_SCOPE_GATE_TEST_BASELINE=1` replaces the hook with a no-op (the pre-gate state) to show the acceptance tests red.
