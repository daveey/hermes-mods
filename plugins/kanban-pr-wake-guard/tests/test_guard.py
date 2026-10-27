import importlib.util
import json
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / '__init__.py'
spec = importlib.util.spec_from_file_location('wake_guard', PATH)
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GuardTests(unittest.TestCase):
    def test_raw_state_refused(self):
        for tool in ('kanban_block', 'kanban_schedule'):
            for probe in (
                'test "$(gh pr view 42 -R example/project --json state -q .state)" = MERGED',
                'gh pr status --json state | jq -e .state',
                'gh api repos/example/project/pulls/42 --jq .merged',
                "gh api /repos/example/project/pulls/42 -q '.merged_at != null'",
                'gh pr view 42 --json mergedAt',
                'gh pr view 42 | grep MERGED',
                'gh --repo example/project pr view 42 --json state',
                'gh pr view 42 --json mergeStateStatus',
            ):
                with self.subTest(tool=tool, probe=probe):
                    result = guard.pre_tool_call(tool, {'wake_check': probe})
                    self.assertEqual(result['action'], 'block')
                    self.assertIn('hermes --run-module hermes_cli.kanban_pr_wake', result['message'])
                    self.assertIn('--superseding-commit', result['message'])
                    self.assertIn('--verification-job', result['message'])

    def test_runtime_probe_passes(self):
        for tool in ('kanban_block', 'kanban_schedule'):
            self.assertIsNone(guard.pre_tool_call(tool, {'wake_check':
                'hermes --run-module hermes_cli.kanban_pr_wake https://github.com/example/project/pull/42'}))

    def test_review_allowed_with_warning(self):
        args = {'wake_check': 'gh pr view 42 --json reviewDecision -q .reviewDecision | grep APPROVED'}
        self.assertIsNone(guard.pre_tool_call('kanban_schedule', args))
        result = guard.transform_tool_result('kanban_schedule', args, json.dumps({'ok': True}))
        parsed = json.loads(result)
        self.assertTrue(parsed['ok'])
        self.assertIn('closes', parsed['warning'])
        self.assertIsNone(guard.transform_tool_result('kanban_schedule', args, result))

    def test_unrelated_allowed(self):
        for probe in ('gh issue view 42 --json state', 'gh api repos/example/project/actions/runs/42 --jq .state',
                      'test -f /tmp/state', '', None):
            self.assertIsNone(guard.pre_tool_call('kanban_block', {'wake_check': probe}))
            self.assertIsNone(guard.transform_tool_result('kanban_block', {'wake_check': probe}, '{}'))
        self.assertIsNone(guard.pre_tool_call('terminal', {'wake_check': 'gh pr view 42 --json state'}))
        self.assertIsNone(guard.pre_tool_call('kanban_block', None))

    def test_deferred_batch(self):
        args = {'calls': [{'name': 'kanban_schedule', 'arguments': {'wake_check': 'gh pr view 42 --json state'}}]}
        self.assertEqual(guard.pre_tool_call('tool_call', args)['action'], 'block')

    def test_common_shell_formatting(self):
        self.assertEqual(guard.classify('gh pr \\\n view 42 --json state'), 'block')
        self.assertEqual(guard.classify('gh pr view 42 --json state # use hermes_cli.kanban_pr_wake later'), 'block')
        self.assertEqual(guard.classify('gh pr view 42 -R example/state --json reviewDecision'), 'warn')
        self.assertEqual(guard.classify('gh api repos/example/merged/pulls/42 --jq .reviewDecision'), 'warn')

    def test_registration(self):
        hooks = {}
        class Context:
            def register_hook(self, name, fn): hooks[name] = fn
        guard.register(Context())
        self.assertEqual(set(hooks), {'pre_tool_call', 'transform_tool_result'})


if __name__ == '__main__':
    unittest.main()
