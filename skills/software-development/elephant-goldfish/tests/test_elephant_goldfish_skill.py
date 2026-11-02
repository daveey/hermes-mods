# Part of the elephant-goldfish skill (derived from google/elephant-goldfish-model). SPDX-License-Identifier: Apache-2.0
"""Packaging checks for the elephant-goldfish skill.

Run from the skill directory:
    python -m unittest discover -s tests -p test_elephant_goldfish_skill.py
"""
import re
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL_MD = SKILL_DIR / "SKILL.md"


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise AssertionError("SKILL.md has no YAML frontmatter block")
    fields = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep and not line.startswith(" "):
            fields[k.strip()] = v.strip().strip('"')
    return fields


def norm(text):
    return re.sub(r"\s+", " ", text)


class FrontmatterTest(unittest.TestCase):
    def setUp(self):
        self.fm = frontmatter(SKILL_MD.read_text())

    def test_name_matches_directory(self):
        self.assertEqual(self.fm.get("name"), SKILL_DIR.name)

    def test_description_follows_index_convention(self):
        d = self.fm.get("description", "")
        self.assertTrue(d.startswith("Use when "), d)
        self.assertLessEqual(len(d), 60, d)
        self.assertNotIn("goldfish", d.lower(), "discoverable without naming EGM")
        for word in ("review", "plan"):
            self.assertIn(word, d.lower())

    def test_license_is_apache(self):
        self.assertEqual(self.fm.get("license"), "Apache-2.0")


class LinkTest(unittest.TestCase):
    def test_every_relative_link_resolves(self):
        checked = 0
        for md in SKILL_DIR.rglob("*.md"):
            for target in re.findall(r"\]\(([^)\s]+)\)", md.read_text()):
                if re.match(r"^[a-z]+://|^#|^mailto:", target):
                    continue
                path = (md.parent / target.split("#")[0]).resolve()
                self.assertTrue(path.exists(), f"{md.relative_to(SKILL_DIR)} -> {target}")
                checked += 1
        self.assertGreaterEqual(checked, 5)

    def test_resources_present(self):
        for rel in ("references/review-rubrics.md", "references/upstream.md",
                    "references/LICENSE.apache-2.0.txt", "templates/design.md", "templates/review.md"):
            self.assertTrue((SKILL_DIR / rel).is_file(), rel)


class TemplateTest(unittest.TestCase):
    def headings(self, rel):
        return set(re.findall(r"^#{2,3} (.+?)\s*$", (SKILL_DIR / rel).read_text(), re.M))

    def test_design_template_fields(self):
        need = {"Problem", "Constraints", "Decisions", "Alternatives", "Non-goals", "Source references",
                "Files", "Invariants", "Acceptance", "Rollback"}
        self.assertLessEqual(need, self.headings("templates/design.md"))

    def test_review_template_fields(self):
        h = self.headings("templates/review.md")
        self.assertLessEqual({"Snapshot", "Reviewer status", "Findings", "Disposition log",
                              "Remaining uncertainty"}, h)
        text = (SKILL_DIR / "templates/review.md").read_text()
        for field in ("sha256", "Source revision", "Reviewer id", "Evidence", "not_run", "incomplete"):
            self.assertIn(field, text)

    def test_rubric_sections(self):
        h = self.headings("references/review-rubrics.md")
        self.assertLessEqual({"Simplicity", "Comprehension", "Design", "Readiness", "Refutation", "Drift"}, h)
        text = norm((SKILL_DIR / "references/review-rubrics.md").read_text())
        self.assertIn("Paraphrase first", text)
        self.assertIn("Zero-decision rule", text)
        self.assertIn("idempotency", text)
        self.assertIn("No drift detected.", text)


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.text = norm(SKILL_MD.read_text())

    def test_flat_parent_owned_dispatch(self):
        self.assertIn("delegate_task", self.text)
        self.assertIn("No nested delegation", self.text)

    def test_allowed_inputs_exclude_conversation(self):
        self.assertIn("allowed input set", self.text)
        self.assertRegex(self.text, r"Do \*\*not\*\* pass the conversation")

    def test_neutral_prompt_and_zero_findings_valid(self):
        self.assertIn("Zero findings is a valid result", self.text)
        self.assertIn("never \"find the bugs\"", self.text)

    def test_isolation_boundary_disclosed(self):
        self.assertIn("not an OS sandbox", self.text)

    def test_failed_reviewer_policy(self):
        self.assertIn("Retry it once", self.text)
        self.assertIn("`not_run`", self.text)
        self.assertIn("`incomplete`", self.text)
        self.assertIn("never \"zero findings\"", self.text)

    def test_bounded_termination(self):
        self.assertIn("**one** remediation/review cycle", self.text)
        self.assertIn("Never call this \"converged\"", self.text)

    def test_drift_is_read_only_and_unscheduled(self):
        self.assertIn("Drift mode (on demand, read-only)", self.text)
        self.assertIn("no recurring drift job", self.text)

    def test_handoff_does_not_authorize_deploy(self):
        self.assertIn("never authorizes deployment", self.text)
        self.assertIn("new implementer", self.text)

    def test_cites_headings_not_cached_lines(self):
        self.assertIn("never by cached line numbers", self.text)


class AttributionTest(unittest.TestCase):
    def test_upstream_pin_and_license(self):
        up = (SKILL_DIR / "references/upstream.md").read_text()
        self.assertIn("403d8881d62022490637d740657b33f01e321ea8", up)
        self.assertIn("Apache License, Version 2.0", up)
        self.assertIn("What changed", up)
        lic = (SKILL_DIR / "references/LICENSE.apache-2.0.txt").read_text()
        self.assertIn("Apache License", lic)
        self.assertIn("Version 2.0, January 2004", lic)


if __name__ == "__main__":
    unittest.main()
