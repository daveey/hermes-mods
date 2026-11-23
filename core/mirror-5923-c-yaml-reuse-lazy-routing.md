# Thread-local safe YAML reuse and lazy spend-routing imports

Patches: **5922–5923**.

## Behavior

Successful safe YAML loaders are reused per thread and parser implementation (`pure` flag), avoiding repeated parser construction while preserving document isolation. Emitters/editors remain operation-owned. Read-only spend diagnostics no longer load model-routing catalogs and the `requests` HTTP client merely by importing spend-decision support, keeping that eager import chain off lanes diagnostics.

## How it works

`hermes_yaml._load` keeps a `threading.local()` dictionary keyed by `pure`. It pops the parser before loading: a busy parser is unavailable for reuse and a nested/reentrant call must obtain a different instance. The loader resets YAML version to 1.1 and clears `doc_infos` before every document. Only successful loads put a parser back. A parse failure therefore discards that instance rather than trusting a parser whose recovery state is unspecified. Busy instances are removed from the available pool, not shared across nested calls or threads; a successful outer load may replace a nested idle pool entry.

The normal safe YAML policy remains, including YAML 1.1 behavior; explicit directive handling retains implementation-specific behavior already exercised by the tests. Resetting document metadata prevents prior versions/directives from contaminating later undirected documents or accumulating history.

`kanban_spend_decision` moves `forwarded_text`/`forwarded_title` imports into `_route`, where routing actually needs them. Read-only diagnostics avoid that model-neutral/catalog/HTTP-client chain, while routing still imports its validators when used. The follow-up explicitly sets subprocess `check=False` in the import regression so the return-code assertion, with stderr, owns failure reporting.

## Code

- `hermes_yaml.py`: thread-local safe-loader pool and per-document reset.
- `hermes_cli/kanban_spend_decision.py`: route-local model-neutral import.
- `tests/test_yaml_parser_reuse.py`, `tests/hermes_cli/test_lanes_cold_import.py`: new regression coverage and explicit child-failure assertion.

## Tests

**Source regression coverage; not executed in this mirror pass.**

- `tests/test_yaml_parser_reuse.py`: parser construction reuse in fresh threads for both pure modes, version reset and bounded `doc_infos`, malformed-parse discard, four-thread/two-mode isolation, alias/tag-directive nonleakage and reentrant active-instance separation.
- `tests/hermes_cli/test_lanes_cold_import.py`: isolated subprocess imports spend decisions and asserts both `hermes_cli.kanban_model_neutral` and `requests` remain absent from `sys.modules`; nonzero child status is asserted with stderr. This is an import-boundary regression, not an end-to-end lanes HTTP latency test.

No YAML benchmark, lanes timing, Desktop timeout experiment or runtime deployment verification was executed here.

## Applying

Use the snapshot replay recipe in `core/README.md`; the loader change depends on the existing shared safe-YAML implementation. For a port, retain separate pools for each thread and `pure` mode, remove active parsers before load, reset metadata and return only successful instances. Preserve route-time validation when moving the spend import.

## Gotchas

Never use a process-global YAML instance or return failed parsers to the pool. Reusing loaders does not make emitters/editors shareable. Do not promise identical explicit-directive behavior between parser implementations. Lazy import removes eager cost; it does not remove the routing dependency or prove all callers avoid `requests`. These patches do not change Desktop timeouts or the separately published lanes stale-while-revalidate plugin.

## plugin?

Core parser and import-graph changes. A plugin cannot safely substitute a global parser cache or alter the shared loader's reentrancy contract.
