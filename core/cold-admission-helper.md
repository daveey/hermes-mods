# Cold kanban admission helper reuses recorded discovery

Patches:

- `patches/0977-perf-kanban-cold-admission-helper-serves-recorded-pl.patch`

A fresh admission helper under macOS background-tier I/O spent 24-44 s validating its first card: provider discovery at config import (5-10 s), loading all plugins to learn whether terminal/file resolve (10-17 s), and re-parsing every SKILL.md frontmatter (4-9 s).

## Behavior

Each is done once and reused by later cold processes with identical results: the `OPTIONAL_ENV_VARS` additions run lazily on first use under a re-entrant lock; `plugins_snapshot` records plugin toolset keys and portable MCP names; skill frontmatter aliases are cached.

## How it works

`config_env_registry.py` holds the lazy registry; `plugins_snapshot.py` the recorded discovery.

## Code

`hermes_cli/config.py`, `config_env_registry.py`, `plugins_snapshot.py`, `kanban_capabilities.py`, `agent/skill_utils.py`.

## Tests

`tests/hermes_cli/test_config_lazy_env_registry.py` plus the snapshot tests in the same commit.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

None.
