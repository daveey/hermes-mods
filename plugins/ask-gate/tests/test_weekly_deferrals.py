import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('weekly_deferral_core', Path(__file__).resolve().parents[1] / 'core.py')
assert spec is not None and spec.loader is not None
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class Deferrals(unittest.TestCase):
    def test_third_person_decision(self):
        for reply in ('The user reads the recommendation and decides the path forward.',
                      'When it completes, the user reads the recommendation and decides the path forward.'):
            self.assertTrue(core.find_asks(reply))
            self.assertTrue(core.find_leftovers(reply))

    def test_direct_reauthorization(self):
        self.assertTrue(core.find_asks('If you\'d rather repair it here, tell me here directly: "repair it".'))

    def test_descriptive_and_negated_statements(self):
        for text in ('The user picks a model from the dropdown and the app saves it.',
                     'Then the user chooses a file.',
                     'When the user decides to reply, the bot answers.',
                     'Neither the agent nor the user decides anything here.',
                     'Nobody needs to act: the user chooses nothing.',
                     'Do not tell me here directly.', "Don't tell me here directly."):
            self.assertFalse(core.find_asks(text), text)
            self.assertFalse(core.find_leftovers(text), text)

    def test_not_a_new_ask(self):
        for reply in ('The user read the recommendation and decided the path forward.',
                      'The agent reads the recommendation and decides the path forward.',
                      'I cannot confirm that the fix worked; the test is still running.',
                      'You already told me here directly to repair it.',
                      'The documentation says "tell me here directly" as an example.'):
            self.assertFalse(core.find_asks(reply), reply)

if __name__ == '__main__':
    unittest.main()
