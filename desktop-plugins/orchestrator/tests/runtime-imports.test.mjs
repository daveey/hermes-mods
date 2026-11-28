import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
test("disk-plugin imports stay on the runtime allowlist", () => {
  const code = readFileSync(new URL("../plugin.js", import.meta.url), "utf8");
  const imports = [
    ...code.matchAll(/(?:from\s*|import\s*\()(['"])([^'"]+)\1/g),
  ].map((m) => m[2]);
  assert.ok(imports.length >= 3);
  for (const name of imports)
    assert.ok(
      ["@hermes/plugin-sdk", "react", "react/jsx-runtime"].includes(name),
      name,
    );
  assert.doesNotMatch(code, /\brequire\s*\(|\beval\s*\(/);
});
