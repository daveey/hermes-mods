# skill-trigger-eval

Measures whether the right skill loads when a user asks for something, and compares skill-description
formats on that data. Every number comes from replaying natural asks against the **real** skills index.

- **Index**: rendered by core's own `agent.prompt_builder.build_skills_system_prompt()` in a child process
  (`render_index.py`). Variants copy every skill root's `SKILL.md`/`DESCRIPTION.md` into an overlay
  `HERMES_HOME`, rewrite only the overridden frontmatter, and render again; `render({}) == render_live()` is
  tested, so the harness can't drift from what the agent sees.
- **Cards**: per skill, `should_load` asks (>= 3) and `near_miss` asks (>= 1, with `instead`: the skill that should
  win, or null). Drafted by the pool LLM from the SKILL.md, the index and real phrasings mined from session history
  (`mine.py`: the user message just before a `skill_view` in human chats; stdlib only, so it runs over ssh on other
  hosts). Owned skills keep their card next to the skill (`references/trigger-cards.yaml`); bundled/upstream
  skills keep theirs in `cards/` here. Cards of private skills never enter this public repo.
- **Router**: one first-turn request per ask to the default chat model via `hermes-pool-llm serve`
  (subscription pool, not paid keys): system = the rendered index (incl. the "load the matching skill"
  instruction), tools = `skill_view` + everyday tools so "no skill" stays an option. Records the `skill_view`
  calls of the first reply. 3 runs per ask; results append to a resumable `results.jsonl`.
- **Scores** (`score.py`): per-skill recall, near-miss pass rate, false loads (loads on near-misses or on other
  skills' asks), collision pairs (two skills alternating across runs of one ask) and stable steals (another skill
  loads instead on >= 2/3 of runs).

## Why a `tools/` dir, not a script under the skill-audit skill

It is a multi-module program with its own tests and a 175-file card corpus; `scripts/test-all` already runs
each `tools/*` dir as its own suite. `skill-audit` points here for the measurement step.

## Run

```sh
cd tools/skill-trigger-eval
P="uv run -q --no-project --with pyyaml python trigger_eval.py"
~/.hermes/plugins/pool-llm/bin/hermes-pool-llm serve --port 8791 &   # once
$P index | head                                                       # the live index, as the agent sees it
python3 mine.py ~/.hermes/state.db > /tmp/mined.json                  # optional; also over ssh on other hosts
$P gen-cards --workers 8 --mine-db /tmp/mined.json                    # drafts into cards/ (skips existing)
$P place-cards --mods-worktree ~/code/mods-work                       # owned cards -> next to their skill
$P run --variant baseline --out results.jsonl --workers 16
$P report --results results.jsonl --variant baseline --out report.md --json metrics.json
$P draft --metrics metrics.json --weakest 30 --owned-only --out formats/    # a2/b/c override files
$P run --variant fmt-a2 --overrides formats/a2.json --skills <list> --out results.jsonl
$P --core <core-wt> run --variant fmt-c --overrides formats/c.json --flag HERMES_SKILL_INDEX_TRIGGERS=1 ...
$P cost --variant base= --variant c=formats/c.json@HERMES_SKILL_INDEX_TRIGGERS=1 --alt-core <core-wt>
```

Core renders a skill's `triggers:` frontmatter on its index row as `Asks: "..". Not for: ..` by default
(config `skills.index_triggers`, default true; daveey/hermes PR #771), so `run --variant after-core` against the
installed core needs no flag. `--flag K=V` sets an env var for the index render only;
`HERMES_SKILL_INDEX_DESC_LIMIT` and `HERMES_SKILL_INDEX_TRIGGERS` were the pre-landing prototype's flags and only
matter for an old prototype worktree passed as `--core`.

Cross-host usage for the retire list: `collect_usage.sh` prints every home's `skills/.usage.json`; `usage.py`
merges dumps (`parse_dump`, `merge`, `idle`).

## Tests

`uv run -q --no-project --with pyyaml --with pytest python -m pytest -q tests` (index rendering through core,
card loader/validation/locations, mining, scoring, format drafts, usage merge). Rendering tests skip without a
core checkout at `~/.hermes/hermes-agent`.
