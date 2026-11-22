"""Offline regression for actionable Kanban/delegate routing guidance.

Scan public skill prose, not benchmark code or historical diagnosis. Exact
interactive chat guidance and diagnostic model observations remain valid.
"""
from pathlib import Path
import re
import unittest

SKILLS = Path(__file__).resolve().parents[3]


class ReviewRoutingPolicy(unittest.TestCase):
    def test_no_exact_or_mandatory_review_recovery_advice(self):
        forbidden = [
            r"pinned to opus",
            r"reviewers are pinned to the strongest model",
            r"Pin\s+`model`/`provider` on \*\*every\*\* refutation",
            r"Repin to a working subscription model",
            r"Repin every card.*to a live provider",
            r"hermes kanban set-model <id> <model> --provider",
            r"mandatory, live-proven independent subscription pin",
            r"review_model`/`review_provider` set to the remaining independent route",
            r"Don't override.*pinned `kanban.default_model`",
        ]
        hits = []
        for path in sorted(SKILLS.rglob('*.md')):
            for pattern in forbidden:
                if re.search(pattern, path.read_text(), re.I):
                    hits.append(f'{path.relative_to(SKILLS)}: {pattern}')
        self.assertEqual(hits, [], '\n'.join(hits))

    def test_design_review_is_capability_routed_and_custody_separated(self):
        text = (SKILLS / 'software-development/elephant-goldfish/SKILL.md').read_text()
        for required in ('class:deep', 'provider omitted', 'never mandatory',
                         'run/session custody', 'supported review handoff'):
            self.assertIn(required, text)


if __name__ == '__main__':
    unittest.main()
