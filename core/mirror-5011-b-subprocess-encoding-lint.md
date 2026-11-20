# Call-aware subprocess encoding lint

Mirror entries: 4478, 4479, 4486.

## Behavior

- **4478** — Multiline subprocess calls with explicit encoding no longer need continuation-line suppression comments.
- **4479** — The final exemption attributes encoding to the same parsed call as text=True, replacing the permissive continuation scan.
- **4486** — A now-stale explicit-encoding suppression is removed from self-work forwarding.

## How it works

The Windows footgun scanner parses a module lazily when a matching rule needs call ownership. The exemption checks that every relevant call passing the matched `text` keyword on that line also has its own direct `encoding` keyword, regardless of continuation-line layout.

Nested or enclosing calls, triple-quoted string content and `**kwargs` cannot supply the exemption. Unparseable source does not exempt. The rule still reports single-line or multiline text-mode calls lacking explicit encoding; the cleaned production call sites retain their encoding arguments.

## Code

- `hermes_cli/kanban_selfwork_outbound.py`
- `hermes_cli/worktree_archive.py`
- `scripts/check-windows-footguns.py`
- `scripts/releases/commit_build.py`

## Tests

The source multiline-subprocess suite covers next-line and later-line encoding, calls with no encoding, incomplete source, unrelated nested/enclosing calls, multiline strings and kwargs. A guard rejects obsolete suppression markers at the explicitly maintained sites. These tests were inspected, not freshly run.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/scripts/test_footgun_multiline_subprocess_encoding.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

The initial parenthesis-based continuation implementation is historical, not the final classifier. Removing suppression comments is safe only when the call itself supplies encoding; nearby text is not evidence.
