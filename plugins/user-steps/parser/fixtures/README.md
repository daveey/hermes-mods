# Differential fixtures against the actual Desktop renderer

`desktop-oracle.json` (schema 1) records, for each case, what Desktop's real renderer showed at `core_commit` (`a960c29ca05d8fb81d2ca0f99d2e1a146059bb19`):

- `desktop.cards`: the directive sources rendered as `user-step` cards.
- `desktop.card_last`: true when nothing visible follows the single card.

It also records `scanner`, the source the committed scanner admits, or `null`. `../check-fidelity.mjs` replays every case through the shipped `scanner.mjs` in fresh Node processes. It requires that the scanner never admits a case Desktop does not render as exactly one final card with that source, and that every verdict equals the recorded one. A changed verdict must be re-recorded deliberately.

## Cases (3783)

- **300 synthetic cases**: 237 in `oracle/cases.mjs`, 47 block-splitting cases in `oracle/cases-blocks.mjs`, and 16 `survivors/*` cases stored directly in the fixture, in these classes:
  - control: ordinary list/prose plans, URLs, www, email, CRLF, ordered/nested lists
  - math: `$`/`$$` on and off, math in attrs, plan, items and before the ask, unclosed display, currency
  - incomplete: constructs that `tailBoundedRemend` repairs (links, images, emphasis, fences, odd dollars)
  - hidden: entities, think blocks, comments, previews
  - nested/nonfinal: directives in lists, quotes, tables, inline text, two directives, a directive followed by text
  - blocks: marked block-splitting boundaries such as HTML blocks, tables, definitions, CR-only and U+2028
  - survivors: image references whose later definitions hide HTML/entity cues from the whole-document AST but not from the first independently parsed block; leading-HTML controls and trailing blank-line controls
- **161 sanitized real assistant replies** (`real_replies`), each in three variants (`real_variants`): the plain reply, the reply plus an ask, and the reply plus a plan and an ask.
  - Sources: finished assistant messages from local request dumps, plus a deterministic every-13th sample of agent-authored Kanban result Markdown up to 4 KB.
  - Sanitization, done before recording by `oracle/sanitize-real.mjs` (a 2026-11-28 snapshot; its sources keep growing, so a rerun samples differently), keeps only structure:
    - Every letter word is replaced by a hashed placeholder word with the same capitalization. Only a 22-word structural allowlist is kept (plan, step, the, ...).
    - Every digit becomes `0`.
    - URLs, emails, paths and hex ids become fixed safe values.
  - No original prose, names, URLs, identifiers or numbers remain.
- **3000 structural fuzz cases** (`fuzz`), regenerated deterministically by `oracle/fuzz.mjs`, so only their verdicts are stored. Each combines random HTML, fence, list, quote, table, definition, setext, math and footnote pieces with plans, separators (LF, CR, CRLF, blank lines) and indented, quoted or listed asks. This class found what the hand-written cases missed: an unclosed raw HTML element (`<a href>`) wraps the following card in a blocked link through rehype-raw. The baseline over-accepted 9 fuzz cases; the first F1 correction still over-accepted 3, which led to the raw-HTML guard.

## Reproducing the oracle

`oracle/replay-oracle.sh` does the following, then compares the result with the recorded verdicts (`oracle/check-oracle.mjs`):

1. `git archive`s Desktop at `core_commit`.
2. Installs Desktop's own pnpm-locked dependencies (`--frozen-lockfile --ignore-scripts`).
3. Renders every case through the exported `MarkdownTextContent` in Desktop's own vitest/jsdom `ui` project (`oracle/user-steps-oracle.test.tsx`).

The only seam is one registry contribution that claims the `user-step` directive, exactly as a plugin does. The oracle shares no dependency installation with the scanner.
