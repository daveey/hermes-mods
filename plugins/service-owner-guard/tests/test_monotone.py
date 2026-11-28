"""Preserve every baseline refusal from the inert source/runtime corpus."""
import json
from pathlib import Path
from unittest import mock

from test_guard import Base


class MonotoneFloor(Base):
    def test_baseline_refusals(self):
        fixture = json.loads((Path(__file__).parent / 'monotone_floor.json').read_text())
        self.assertEqual(fixture['counts']['new_allows'], 0)
        with mock.patch.object(self.g, '_pinned', return_value=('', '')):
            for case in fixture['refusals']:
                with self.subTest(command=case['command'], lookup=case['lookup']), \
                     mock.patch.object(self.g, '_lookup', return_value=case['lookup']):
                    tool = 'execute_code' if case['python'] else 'terminal'
                    key = 'code' if case['python'] else 'command'
                    self.assertIsNotNone(self.g.classify(tool, {key: case['command']}))
