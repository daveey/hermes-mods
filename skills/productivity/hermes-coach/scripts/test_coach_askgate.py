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
          "do it", "yes, figure it out",
          # third-party agent (Baby Bear), not Hermes asking David
          "why didnt the agent figure it out even with failing search? it could have just listed the devices",
          # David handing off unprompted, not correcting an ask
          "done. not sure if i hit the phases, listen yourself and possible split things up"]


class T(unittest.TestCase):
    def test_hits(self):
        for t in HITS:
            self.assertTrue(g.classify(g.clean(t)), t)

    def test_misses(self):
        for t in MISSES:
            self.assertIsNone(g.classify(g.clean(t)), t)

    def test_opening_turn_skipped(self):
        import json, tempfile, os
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(f"{d}/mini/default")
            turns = [{"t": 2000, "text": "whats this, i want it to stop asking me [screenshot]"},
                     {"t": 2100, "text": "why are you asking me to run things"}]
            json.dump({"id": "s1", "host": "mini", "user_turns": turns}, open(f"{d}/mini/default/s1.json", "w"))
            # text-only opener answering a carried-over ask still counts
            json.dump({"id": "s2", "host": "nous-1", "user_turns": [{"t": 3000, "text": "i dont plan to review. you should merge it yourself"}]},
                      open(f"{d}/mini/default/s2.json", "w"))
            hits, scanned = g.collect(d, 0, 10**10)
            self.assertEqual([h["quote"] for h in hits],
                             ["why are you asking me to run things", "i dont plan to review. you should merge it yourself"])


if __name__ == "__main__":
    unittest.main()
