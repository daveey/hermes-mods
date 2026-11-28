# Desktop parser bridge

Runtime: `node scanner.mjs`, JSON stdin `{ "reply": "..." }`, one JSON result or `null` on stdout. No npm install is needed at runtime. Requires Node >=18 (tested with Node 22 and 26). `ask.text` is the Desktop paragraph's decoded directive text; `ask.source` and `ask.attrs` come directly from `segmentTranscriptDirectives`. `plan` is the **entire preprocessed prefix** before the final paragraph, with only trailing whitespace trimmed. The Python caller must apply its existing prefix size limits and attribute/schema validation, and validate every `manifest.json.modules` SHA-256 against its core root plus `bundle_sha256` before running the bundle.

**Required caller bounds.** The scanner has no internal time limit below its 200000-character cap, and some shapes (deep blockquotes, long `*_` runs) are super-linear. Every caller must cap input and wall time and treat a timeout as `null`. The current caller (`../confirmations.py`) caps replies at 65536 characters and the subprocess at 3 seconds, failing closed.

Rebuild inside this directory (npm scripts disabled, locked graph):

```
npm ci --ignore-scripts --no-audit --no-fund
HERMES_CORE_GIT=/path/to/hermes-agent HERMES_CORE_COMMIT=<full sha> npm run build
npm test
```

## What the scanner mirrors

Every core input is read with `git cat-file blob <commit>:<path>` from `HERMES_CORE_GIT` (default `$HOME/.hermes/hermes-agent`) at `HERMES_CORE_COMMIT` (default its `HEAD`), never from a working tree, so `manifest.json.core_commit` names exactly the bytes whose hashes are recorded. Nothing is written unless every check passes.

The pipeline is Desktop's own wiring from `markdown-text.tsx`, selected by TypeScript AST (original declarations, plus exactly the import specifiers they use; no copy, no shim): `mathPlugin` (`createMemoizedMathPlugin({ singleDollarTextMath: true })` from `katex-memo.ts`), `REMARK_PLUGINS` (`Object.values(streamdown.defaultRemarkPlugins)`, `remarkTranscriptDirectives`, `remarkSoftBreaks`) and `preprocessWithTailRepair` (`preprocessMarkdown` then `@assistant-ui/react-streamdown`'s `tailBoundedRemend`). `katex-memo.ts`, `lru-cache.ts` and `markdown-blocks.ts` are bundled from full source. Streamdown 2.7.0 composes each block's remark chain as `[...remarkPlugins, plugins.math.remarkPlugin]`; the scanner uses `remark-parse` + `REMARK_PLUGINS` + `mathPlugin.remarkPlugin` in that order.

Desktop renders finished replies through three stages the scanner handles as follows:

- **remark-math** (`singleDollarTextMath`): mirrored. Math nodes holding `::` are doubt; a display block changes what the preceding plan block is.
- **Unconditional `tailBoundedRemend`**: not used to repair. The scanner computes Desktop's `preprocessWithTailRepair(reply)` and refuses whenever it differs from `preprocessMarkdown(reply)`. An ask is admitted only when Desktop renders the unrepaired text, so no repair can create, move, extend or close an actionable directive. This refuses some replies Desktop would render with a card (unclosed `$$`, fences or emphasis elsewhere); that is the intended conservative subset.
- **marked block splitting** (`parseMarkdownIntoBlocksCached`): mirrored. Streamdown renders every block as an independent document. The blocks must reproduce the text (marked normalizes CR/CRLF to LF). No block but the last non-blank one may hold doubt when parsed alone, and the last block parsed alone must still yield a sole final directive. An earlier block cannot hold a directive, because the reply must contain exactly one `::`. The whole-document checks still apply as well, so admission is the AND of both views. This is not hypothetical: marked and micromark disagree about some fence lines (for example "```~"), and Desktop then renders the ask inside a code block. A structural fuzz of 40,000 fence shapes found 15 such replies; the baseline accepted all of them, Desktop shows no card for any, and only the last-block check refuses them (`fixtures/oracle/cases-blocks.mjs`). A fresh process has no streaming lineage, so the cached splitter performs a full lex; `markdown-blocks.ts` documents its incremental path as byte-identical to that.

Directive occurrence counting is across the full AST, not only root paragraphs. Additional raw `::` cues, including hidden reasoning and code examples, fail closed conservatively. **Raw HTML** (mdast `html` nodes) other than a self-contained comment or a void element (`br`, `hr`, `img`, `wbr`) refuses: Desktop renders HTML through rehype-raw, where an unclosed element (for example `<a href="x">`) wraps everything after it, including the card, into a blocked link. The actual-Desktop fuzz corpus demonstrated this over-accept on the baseline and on the first F1 correction. Unknown directives, malformed/duplicate attributes, nesting, inline surrounding text, nonfinal asks, and insufficient preceding plans return null. No confirmation wording is matched. List plans require at least two items of at least four words **each**; prose requires 25 words and an immediately preceding heading containing `plan`.

`fixtures/desktop-oracle.json` records, for every synthetic, sanitized real and fuzz case (3767), what the actual Desktop surface rendered at `core_commit` (see `fixtures/README.md`). `fidelity.test.mjs` requires that the scanner never accepts a case Desktop does not render as exactly one final card with the same source, and that every verdict matches the recorded one.

## Dependency identity

`package.json` pins direct dependencies and uses npm `overrides` so that the resolved graph equals Desktop's. Equality is enforced, not assumed: `lockgraph.mjs` walks every package instance with bytes in the bundle through the import edges esbuild actually resolved, and each edge must exist in Desktop's `pnpm-lock.yaml` at `core_commit` (importer `apps/desktop`, then `snapshots`) with the same version **and** integrity; `unified` and `remark-parse` imported by `scanner.ts` are checked as Streamdown's. Any missing edge, unreached package, version or integrity difference fails the build. The checked table is recorded in `manifest.json.dependencies` (path, version, integrity, Desktop snapshot keys); packages parsed but fully tree-shaken are listed in `tree_shaken_packages`. `pnpm-lock.yaml` and `apps/desktop/package.json` are hashed into `manifest.json.modules`, so the runtime caller refuses when Desktop's locked graph changes until the bundle is rebuilt.

## Provenance and licenses

Core sources: NousResearch/hermes-agent fork at `manifest.json.core_commit`; Hermes upstream license is MIT. `THIRD-PARTY-LICENSES.txt` contains original installed license texts for every bundled npm package instance, keyed by its full `node_modules` path, plus the build tools (esbuild, typescript, yaml); not all packages are MIT and each retains its own license. `remark-math`'s npm tarball omits its license; `remark-math-LICENSE` is the upstream monorepo MIT license fetched from https://raw.githubusercontent.com/remarkjs/remark-math/main/license and kept for offline rebuilds. `inspect-deps.mjs` prints the installed Streamdown default remark plugin names/options.

Tests: `smoke.mjs` (original 16 cases plus prefix preservation) and `fidelity.test.mjs` run the actual bundle in fresh Node subprocesses; `../tests/test_structured_parser.py` runs both and checks runtime manifest refusal for every hashed input.
