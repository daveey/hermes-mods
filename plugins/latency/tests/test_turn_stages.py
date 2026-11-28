"""A stage bar describes one trace, never a sum of marginal percentiles."""
import importlib
import itertools
import unittest

from test_api import api


def stage(name, at, span_id, parent: str | None='1'*16, trace='a'*32, end=None):
    return dict(metric='turn.'+name, trace_id=trace, span_id=span_id,
                **({'parent_span_id': parent} if parent else {}),
                start_time_unix_nano=str(at), end_time_unix_nano=str(at if end is None else end))


def complete():
    start = 1_000_000_000
    return [stage('send', start, '1'*16, None, end=start+100_000_000),
            stage('hub_accepted', start+10_000_000, '2'*16),
            stage('model_admitted', start+30_000_000, '3'*16),
            stage('first_token', start+90_000_000, '4'*16),
            stage('painted', start+100_000_000, '5'*16)]


class TurnStages(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module(api.pkg().__name__+'.turn_stages')

    def test_order_independent_exact_sum_and_correct_parent_links(self):
        for rows in itertools.permutations(complete()):
            result = self.module.join(rows)
            self.assertEqual([s['dur_ms'] for s in result['stages']], [10, 20, 60, 10])
            self.assertEqual(result['end_to_end_ms'], 100)
            self.assertEqual(result['accounted_ms'], 100)
            self.assertTrue(result['complete'])

    def test_every_missing_stage_stays_unmeasured(self):
        rows = complete()
        for index in range(len(rows)):
            with self.subTest(missing=index):
                result = self.module.join(rows[:index]+rows[index+1:])
                self.assertFalse(result['complete'])
                self.assertTrue(any(s['dur_ms'] is None for s in result['stages']))
                self.assertIsNone(result['accounted_ms'])
        self.assertEqual([s['dur_ms'] for s in self.module.join([])['stages']], [None]*4)

    def test_trace_and_parent_mismatch_do_not_join(self):
        for field, bad in [('trace_id', 'b'*32), ('parent_span_id', '9'*16)]:
            rows = complete()
            rows[2][field] = bad
            result = self.module.join(rows)
            self.assertIsNone(result['stages'][1]['dur_ms'])
            self.assertIsNone(result['stages'][2]['dur_ms'])

    def test_identical_replay_is_safe_but_conflicting_duplicate_fails_closed(self):
        rows = complete()
        self.assertTrue(self.module.join(rows+rows)['complete'])
        other = {**rows[2], 'span_id': '9'*16}
        self.assertFalse(self.module.join(rows+[other])['complete'])
        self.assertFalse(self.module.join(rows+[{**rows[0], 'span_id': '9'*16}])['complete'])

    def test_clock_regression_and_outside_root_are_not_zero(self):
        for at in (999_000_000, 1_200_000_000, 1_005_000_000):
            rows = complete()
            rows[2]['start_time_unix_nano'] = str(at)
            rows[2]['end_time_unix_nano'] = str(at)
            result = self.module.join(rows)
            self.assertFalse(result['complete'])
            self.assertTrue(any(s['dur_ms'] is None for s in result['stages']))

    def test_measured_zero_is_not_missing_and_bad_ids_fail_closed(self):
        rows = complete()
        rows[1]['start_time_unix_nano'] = rows[0]['start_time_unix_nano']
        rows[1]['end_time_unix_nano'] = rows[0]['start_time_unix_nano']
        self.assertEqual(self.module.join(rows)['stages'][0]['dur_ms'], 0)
        rows[0]['trace_id'] = '0'*32
        self.assertFalse(self.module.join(rows)['complete'])

    def test_same_span_id_cannot_name_different_stages(self):
        rows = complete()
        rows[2]['span_id'] = rows[1]['span_id']
        result = self.module.join(rows)
        self.assertFalse(result['complete'])
        self.assertEqual([s['dur_ms'] for s in result['stages']], [None, None, None, 10])

    def test_painted_must_agree_with_root_end(self):
        rows = complete()
        rows[-1]['start_time_unix_nano'] = str(1_099_000_000)
        rows[-1]['end_time_unix_nano'] = str(1_099_000_000)
        self.assertFalse(self.module.join(rows)['complete'])
