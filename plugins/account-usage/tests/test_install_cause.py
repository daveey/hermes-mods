"""install() failures name their real cause; a second install in one process is not drift.

Field case (MacBook, 2026-10-30): live processes logged "core changed under its audited
seams" for every install() exception, including ones that are not drift (a core module
the interpreter cannot import, a second load over seams the first already patched).
"""
import builtins
import json
import logging
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import test_execution
from test_load import Ctx, load_plugin  # noqa: E402

PLUGIN = Path(__file__).parents[1]
DRIFT = 'core changed under its audited seams'
SECRET = 'sk-ant-FIXTURE-SECRET'


def failing_core_import(missing):
    real = builtins.__import__

    def fake(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'agent' and fromlist and 'client_lifecycle' in fromlist:
            raise ModuleNotFoundError(f"No module named '{missing}' {SECRET}", name=missing)
        return real(name, globals, locals, fromlist, level)
    return fake


class InstallCause(test_execution.Execution):
    def integration(self):
        integration = self.s.ExecutionIntegration(observe=lambda *args: None)
        self.addCleanup(integration.unload)
        self.addCleanup(self.s._install_failures.pop, integration.home, None)
        return integration

    def test_unimportable_core_leaves_selection_inactive_not_off(self):
        # A bare interpreter (hermes-sync's plugin-cache warm runs core's stale venv, which has
        # no psutil) cannot run an agent turn, so there is nothing to refuse: selection stays
        # inactive and quiet instead of printing "Account selection is off" on every warm.
        integration = self.integration()
        with patch.object(builtins, '__import__', failing_core_import('psutil')):
            with self.assertRaises(ModuleNotFoundError):
                integration.install()
        self.assertEqual(integration.failure[:2], ('ModuleNotFoundError', 'core-imports (psutil)'))
        self.assertTrue(integration.core_unimportable)

    def test_register_stays_quiet_when_core_is_unimportable(self):
        plugin = load_plugin('account_usage_cause_register')
        ctx = Ctx()
        self.addCleanup(lambda: [fn() for fn in reversed(ctx.cleanup)])
        self.addCleanup(self.s._install_failures.clear)
        with patch.object(builtins, '__import__', failing_core_import('psutil')), \
                self.assertLogs(level=logging.INFO) as logs:
            plugin.register(ctx)
        text = '\n'.join(logs.output)
        self.assertIn('ModuleNotFoundError at core-imports (psutil)', text)
        self.assertNotIn('Account selection is off', text)
        self.assertNotIn(SECRET, text)
        self.assertEqual(self.s._install_failures, {})
        self.assertEqual(len(ctx.middleware), 1)  # observation stays registered

    def test_other_install_failures_still_log_a_warning_with_type_and_seam(self):
        plugin = load_plugin('account_usage_cause_register2')
        ctx = Ctx()
        self.addCleanup(lambda: [fn() for fn in reversed(ctx.cleanup)])
        self.addCleanup(self.s._install_failures.clear)
        with patch.object(self.s, 'check_pins', side_effect=RuntimeError(SECRET)), \
                self.assertLogs(level=logging.WARNING) as logs:
            plugin.register(ctx)
        text = '\n'.join(logs.output)
        self.assertIn('install raised RuntimeError at execution-compatibility.json', text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(DRIFT, text)

    def test_pin_mismatch_keeps_the_drift_text_and_names_the_file(self):
        integration = self.integration()
        with self.assertRaises(self.s.SelectionRejected):
            self._install_with_pins_into(integration, lambda value: ['0' * 64])
        integration.disable()
        self.assertIn(DRIFT, integration.error)
        self.assertRegex(integration.error, r'\((agent/[\w/]+\.py|[\w/.]+\.py)\)')

    def _install_with_pins_into(self, integration, rewrite):
        import tempfile
        pins = json.loads((PLUGIN / 'execution-compatibility.json').read_text())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / 'execution-compatibility.json').write_text(
            json.dumps({rel: rewrite(value) for rel, value in pins.items()}))
        with patch.dict(vars(self.s), {'__file__': str(Path(tmp.name) / 'selection.py')}):
            integration.install()

    def test_second_install_in_one_process_adopts_the_first_patch(self):
        from agent.client_lifecycle import ClientLifecycleMixin
        original = ClientLifecycleMixin._create_request_anthropic_client
        first, second = self.integration(), self.integration()
        first.install()
        patched = ClientLifecycleMixin._create_request_anthropic_client
        second.install()  # RED before the fix: SelectionRejected "already customized"
        self.assertTrue(second.active)
        self.assertIsNone(second.error)
        self.assertNotIn(second.home, self.s._install_failures)
        self.assertIs(ClientLifecycleMixin._create_request_anthropic_client, patched)
        second.unload()
        self.assertIs(ClientLifecycleMixin._create_request_anthropic_client, patched)  # first still owns it
        first.unload()
        self.assertIs(ClientLifecycleMixin._create_request_anthropic_client, original)

    def test_concurrent_installs_in_two_threads_both_succeed(self):
        from agent.client_lifecycle import ClientLifecycleMixin
        original = ClientLifecycleMixin._create_request_anthropic_client
        integrations = [self.integration() for _ in range(4)]
        errors, start = [], threading.Barrier(len(integrations))

        def run(integration):
            start.wait()
            try:
                integration.install()
            except Exception as exc:
                errors.append(type(exc).__name__)
        threads = [threading.Thread(target=run, args=(i,)) for i in integrations]
        [t.start() for t in threads]
        [t.join(60) for t in threads]
        self.assertEqual(errors, [])
        self.assertTrue(all(i.active for i in integrations))
        for i in integrations:
            i.unload()
        self.assertIs(ClientLifecycleMixin._create_request_anthropic_client, original)

    def test_a_foreign_wrapper_on_a_seam_is_still_refused(self):
        from agent.client_lifecycle import ClientLifecycleMixin
        original = ClientLifecycleMixin._create_request_anthropic_client
        first, second = self.integration(), self.integration()
        first.install()
        foreign = lambda *args, **kw: None
        with patch.object(ClientLifecycleMixin, '_create_request_openai_client', foreign):
            with self.assertRaises(self.s.SelectionRejected):
                second.install()
        second.disable()
        self.assertIn('install raised SelectionRejected at _create_request_openai_client', second.error)
        first.unload()
        self.assertIs(ClientLifecycleMixin._create_request_anthropic_client, original)


class CheckerAgreesWithInstall(unittest.TestCase):
    """check_compat.py and install() share pins.pin_drift: no input makes one pass and the other refuse."""

    def test_same_verdict_for_every_pin_shape(self):
        import importlib.util
        import tempfile
        def load(name, file):
            spec = importlib.util.spec_from_file_location(name, PLUGIN / file)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module
        check_compat = load('account_usage_cause_check', 'check_compat.py')
        selection = load('account_usage_cause_selection', 'selection.py')
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a.py').write_bytes(b'x = 1\n')
            good = hashlib.sha256(b'x = 1\n').hexdigest()
            for label, pins in {'string': {'a.py': good}, 'list': {'a.py': ['0' * 64, good]},
                                'wrong': {'a.py': '0' * 64}, 'empty-list': {'a.py': []},
                                'int': {'a.py': 5}, 'missing-file': {'b.py': good}, 'empty-table': {},
                                'list-with-int': {'a.py': [good, 5]}}.items():
                with self.subTest(label):
                    plugin = root / 'plugin'
                    plugin.mkdir(exist_ok=True)
                    (plugin / 'execution-compatibility.json').write_text(json.dumps(pins))
                    checker_ok = check_compat.check(root, plugin=plugin)['ok'] if pins else False
                    try:
                        selection.check_pins(root, pins)
                        install_ok = True
                    except selection.SelectionRejected:
                        install_ok = False
                    self.assertEqual(checker_ok, install_ok)


if __name__ == '__main__':
    unittest.main()
