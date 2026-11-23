# Desktop parser bridge

Runtime: `node scanner.mjs`, JSON stdin `{ "reply": "..." }`, one JSON result or `null` on stdout. No npm install is needed at runtime. Requires Node >=18 (verified with Node 22). `ask.text` is the Desktop paragraph's decoded directive text; `ask.source` and `ask.attrs` come directly from `segmentTranscriptDirectives`. `plan` is the **entire preprocessed prefix** before the final paragraph, with only trailing whitespace trimmed. The Python caller must apply its existing prefix size limits and attribute/schema validation, and validate every `manifest.json.modules` SHA-256 against its core root plus `bundle_sha256` before running the bundle.

Rebuild inside this directory:

```
npm ci --ignore-scripts --no-audit --no-fund
HERMES_CORE_ROOT=/path/to/hermes-agent npm run build
npm test
```

The default source root is `$HOME/.hermes/hermes-agent`. The build reads actual Desktop `markdown-preprocess.ts`, `transcript-directives.ts`, and their pure dependencies, without copying or reimplementing their parsers. `media.ts` pulls in browser API/store/i18n dependencies; the build uses the TypeScript AST to select its exact original pure declarations (`MEDIA_BY_EXT`, `pathExtension`, `mediaInfo`, `mediaKind`, `mediaMarkdownHref`). It similarly selects `PREVIEW_MARKDOWN_RE`, `stripPreviewTargets`, and `previewMarkdownHref` from `preview-targets.ts`. These are source selection, not replacement shims. Whole originating module hashes are recorded. All other core imports are bundled from their full source.

The preprocessing math helper is resolved to the actual installed `@assistant-ui/react-streamdown/dist/preprocess.js` export rather than its React entry point. Remark wiring matches `markdown-text.tsx`: remark-parse, actual `Object.values(streamdown.defaultRemarkPlugins)` (GFM and codeMeta in 2.7.0), `remarkTranscriptDirectives`, `remarkSoftBreaks`. No invented additional remark-math stage. Streaming remend is intentionally NOT run on finished replies: incomplete directive syntax must fail closed instead of being repaired into an actionable ask.

Directive occurrence counting is across the full AST, not only root paragraphs. Additional raw `::` cues, including hidden reasoning and code examples, fail closed conservatively. Unknown directives, malformed/duplicate attributes, nesting, inline surrounding text, nonfinal asks, and insufficient preceding plans return null. No confirmation wording is matched. List plans require at least two items of at least four words **each**; prose requires 25 words and an immediately preceding heading containing `plan`.

## Provenance and licenses

Core sources: NousResearch/hermes-agent fork identified by `manifest.json.core_commit`; exact module hashes in manifest. Hermes upstream license is MIT. npm versions and integrity values are frozen in `package-lock.json`; dependency versions mirror Desktop for parser/runtime packages. `THIRD-PARTY-LICENSES.txt` contains original installed license texts for every bundled npm package and build tools (not all transitive packages are MIT; each retains its own license). `remark-math`'s npm tarball omits its license; `remark-math-LICENSE` is the upstream monorepo MIT license fetched from https://raw.githubusercontent.com/remarkjs/remark-math/main/license and retained locally for offline rebuilds. It is not currently a bundled parser stage. `inspect-deps.mjs` prints the installed Streamdown default remark plugin names/options.

Smoke tests exercise the actual portable bundle in fresh Node subprocesses, including valid list/prose plans, signed-style URLs, prefix freezing, nesting, duplicates, malformed attrs, and non-directive confirmation text.
