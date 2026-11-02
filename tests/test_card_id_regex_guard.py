"""Guard: no card-id regex in plugin/skill source that matches exactly 8 hex.

Kanban card ids grew from ``t_`` + 8 hex to ``t_`` + 16 hex. A pattern written for the old length either never
matches a current id (with ``\\b`` on both ends) or cuts it to its first 8 hex. Cut-short ids collide: cards made
minutes apart share their first 8 hex (``t_01a19e09ae7c3790`` and ``t_01a19e09aec77ab4``). Several sites were fixed
one at a time while others kept the old pattern, so this scan fails on any new one.

Allowed: ``t_[0-9a-f]{8}(?:[0-9a-f]{8})?`` (legacy and current ids, never a prefix), ``{8,}``, ``{8,16}``, ``{16}``,
and the BRE spelling ``t_[0-9a-f]\\{8\\}\\([0-9a-f]\\{8\\}\\)\\?``. Excluded: ``core/patches/`` (upstream diffs),
test files and fixtures, and Markdown prose.

Run: ``python -m pytest tests/test_card_id_regex_guard.py`` or ``python -m unittest tests/test_card_id_regex_guard.py``.
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEX = r"\[(?:0-9a-f|a-f0-9)\]"
# ``t_[0-9a-f]{8}`` or ``t_[0-9a-f]\{8\}`` not followed by the optional second 8 hex.
EIGHT_ONLY = re.compile(
    r"t_" + HEX + r"(?:\{8\}|\\\{8\\\})"
    r"(?!\(\?:" + HEX + r"\{8\}\)\?|\(" + HEX + r"\{8\}\)\?|\\\(" + HEX + r"\\\{8\\\}\\\)\\\?)"
)
EXCLUDE_PREFIX = ("core/patches/",)
SKIP_SUFFIXES = (".md", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".lock")


def is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return (any(p in ("tests", "test", "fixtures", "testdata", "__tests__") for p in parts[:-1])
            or name.startswith("test_") or name.endswith(("_test.py", ".test.mjs", ".test.js", ".test.ts",
                                                           ".spec.js", ".spec.mjs", ".spec.ts"))
            or name in ("conftest.py",))


def source_files(root: Path = ROOT):
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout
    for rel in out.splitlines():
        if rel.startswith(EXCLUDE_PREFIX) or is_test_path(rel) or rel.lower().endswith(SKIP_SUFFIXES):
            continue
        p = root / rel
        if p.is_file() and not p.is_symlink():
            yield rel, p


def offenders(root: Path = ROOT):
    bad = []
    for rel, p in source_files(root):
        try:
            text = p.read_text(errors="strict")
        except (UnicodeDecodeError, OSError):
            continue  # binary
        for n, line in enumerate(text.splitlines(), 1):
            if EIGHT_ONLY.search(line):
                bad.append(f"{rel}:{n}: {line.strip()[:120]}")
    return bad


class CardIdRegexGuard(unittest.TestCase):
    def test_detector_flags_exact_8_hex_forms(self):
        for bad in (r're.compile(r"\bt_[0-9a-f]{8}\b")', r"re.fullmatch(r't_[0-9a-f]{8}', x)",
                    r"const ID_RE = /\bt_[0-9a-f]{8}\b/g", r"grep -o 't_[0-9a-f]\{8\}'",
                    r"(unblock|unblocks) (it|t_[0-9a-f]{8})\b", r"t_[a-f0-9]{8}"):
            self.assertTrue(EIGHT_ONLY.search(bad), bad)

    def test_detector_allows_full_id_forms(self):
        for good in (r"\bt_[0-9a-f]{8}(?:[0-9a-f]{8})?\b", r"t_[0-9a-f]{8,}", r"t_[0-9a-f]{8,16}",
                     r"t_[0-9a-f]{16}", r"t_[0-9a-f]+", r"t_[0-9a-f]{6,}",
                     r"grep -o 't_[0-9a-f]\{8\}\([0-9a-f]\{8\}\)\?'", r"t_[0-9a-f]{8}([0-9a-f]{8})?"):
            self.assertFalse(EIGHT_ONLY.search(good), good)

    def test_test_paths_are_excluded(self):
        self.assertTrue(is_test_path("plugins/open-asks/tests/test_open_asks.py"))
        self.assertTrue(is_test_path("desktop-plugins/task-mentions/tests/refs.test.mjs"))
        self.assertTrue(is_test_path("tests/test_card_id_regex_guard.py"))
        self.assertFalse(is_test_path("plugins/open-asks/core.py"))
        self.assertFalse(is_test_path("desktop-plugins/task-mentions/plugin.js"))

    def test_no_8_hex_only_card_id_regex_in_source(self):
        bad = offenders()
        self.assertEqual(bad, [], "card-id regex matching exactly 8 hex (use t_[0-9a-f]{8}(?:[0-9a-f]{8})?):\n"
                         + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
