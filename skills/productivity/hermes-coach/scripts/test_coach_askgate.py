import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import coach_askgate as g

HITS = ["why did you ask me to do cloudlfare work? how do i get you to do it yourself and not ask me?",
        "superhuman has been revoked for a while, i keep completing it",
        "YOU JUST SAID IT WAS CANCELED, WHY DO YOU KEEP ASKING ME",
        "why didnt you do this yourself, without me telling you",
        "are you waiting for me? i dont see any blocked cards",
        "[OUT-OF-BAND USER MESSAGE — x]\nWHY DO YOU KEEP ASKING ME\n[/OUT-OF-BAND USER MESSAGE]"]
MISSES = ["i keep asking what time is it and getting no reply",
          "When contacting someone for the first time, intro yourself as Babybear",
          "i want to give you the rights to do it yourself, give me those instructions",
          "[Slack engagement eng_1 · #x] why are you asking me",
          "Without any other tools: call kanban_comment ... do NOT send it yourself",
          "do it", "yes, figure it out"]


class T(unittest.TestCase):
    def test_hits(self):
        for t in HITS:
            self.assertTrue(g.classify(g.clean(t)), t)

    def test_misses(self):
        for t in MISSES:
            self.assertIsNone(g.classify(g.clean(t)), t)


if __name__ == "__main__":
    unittest.main()
