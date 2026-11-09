# Diké catalog corpus annotation

Patch **2056**.

## Behavior

The catalog entry now explains that the pinned Diké corpus bundles readable JSON, JSON-LD context and Turtle RDF data. Despite the commit's readmission wording, its actual diff is a **comment-only change**: the repository URL, pinned SHA and plugin description are unchanged.

## How it works

A YAML comment immediately above `sha` records the corpus formats for catalog review against the already-landed RDF scanner. This patch does not implement a scanner, change catalog acceptance logic, install the plugin or change its legal-verification behavior.

## Code

`plugin-catalog/dike.yaml`; pinned SHA remains `9780e467ec31d4635eb559e4c09bd49f17e178de`.

## Tests

**Not rerun this pass.** No tests are added or modified in 2056. Catalog scanning/readmission and plugin installation/tool execution are **untested in this patch**; a comment is not evidence of a successful scanner run.

## Applying

Apply 2056 as part of [the core series](README.md). It changes catalog documentation only and relies on the existing scanner implementation.

## Gotchas

Do not infer a new release, repin, runtime capability or security guarantee from this comment. The catalog description's legal-advice and subprocess limitations remain unchanged.

## Plugin eligibility

The described product is already an external plugin; this patch is repository catalog metadata, not an agent-core feature that needs conversion into a plugin.
