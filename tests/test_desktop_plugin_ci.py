"""Keep the desktop Node suites in CI without a private desktop checkout."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DesktopPluginCI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / '.github/workflows/desktop-plugin-tests.yml').read_text()

    def test_hosted_read_only_bounded_job(self):
        code = '\n'.join(line.split('#', 1)[0] for line in self.text.splitlines())
        self.assertNotIn('self-hosted', code)
        self.assertEqual(re.findall(r'^\s*runs-on: (.+)$', code, re.M), ['ubuntu-latest'])
        self.assertIn('contents: read', code)
        self.assertIn('timeout-minutes: 10', code)
        self.assertNotIn('continue-on-error', code)

    def test_push_and_pr_watch_plugins_and_producer_fixture(self):
        for event in ('push', 'pull_request'):
            section = re.search(
                rf'^  {event}:\n(.*?)(?=^  \w|^permissions:)', self.text, re.M | re.S).group(1)
            for path in ('desktop-plugins/**', 'plugins/hub-sync/tests/fixtures/**',
                         '.github/workflows/desktop-plugin-tests.yml', 'tests/test_desktop_plugin_ci.py'):
                self.assertIn(f"- '{path}'", section)
        self.assertIn('branches: [main]', self.text)
        self.assertIn('workflow_dispatch:', self.text)

    def test_isolated_pinned_dependencies_and_all_suites(self):
        self.assertIn('HERMES_DESKTOP_DIR: ${{ runner.temp }}/desktop-test-deps', self.text)
        self.assertIn('node-version: \'22.23.4\'', self.text)
        self.assertIn('npm install --prefix "$HERMES_DESKTOP_DIR"', self.text)
        for package in ('react@19.3.0', 'react-dom@19.3.0', 'jsdom@29.1.1',
                        '@tanstack/react-query@5.104.1', 'radix-ui@1.7.0'):
            self.assertIn(package, self.text)
        self.assertIn('HERMES_DESKTOP_PACKAGE=$HERMES_DESKTOP_DIR/package.json', self.text)
        globs = 'desktop-plugins/*/test/*.mjs desktop-plugins/*/tests/*.test.mjs plugins/*/tests/*.test.mjs'
        self.assertIn(f'          node --test --test-concurrency=1 {globs}\n', self.text)
        # node --test's TAP output does not name files: the job lists what it collected.
        self.assertIn(f"          printf 'collected: %s\\n' {globs}\n", self.text)
        files = list(ROOT.glob('desktop-plugins/*/test/*.mjs'))
        files += ROOT.glob('desktop-plugins/*/tests/*.test.mjs')
        files += ROOT.glob('plugins/*/tests/*.test.mjs')
        self.assertIn(ROOT / 'desktop-plugins/session-panel/test/producer-composed.test.mjs', files)
        self.assertIn(ROOT / 'desktop-plugins/runners/tests/fleet.test.mjs', files)
        # Agent-plugin dirs carry node suites for their desktop half; CI once collected none of them.
        self.assertIn(ROOT / 'plugins/session-mentions/tests/user-terminal.test.mjs', files)

    def test_every_node_test_file_is_globbed(self):
        globbed = set(ROOT.glob('desktop-plugins/*/test/*.mjs')) | set(ROOT.glob('desktop-plugins/*/tests/*.test.mjs'))
        globbed |= set(ROOT.glob('plugins/*/tests/*.test.mjs'))
        for pattern in ('desktop-plugins/*/test*/*.test.mjs', 'plugins/**/*.test.mjs'):
            for f in ROOT.glob(pattern):
                self.assertIn(f, globbed)
        self.assertIn(ROOT / 'desktop-plugins/user-steps/test/decision-queue.test.mjs', globbed)
        self.assertIn(ROOT / 'desktop-plugins/user-steps/test/kanban-question.test.mjs', globbed)

    def test_no_node_suite_hides_at_desktop_plugin_root(self):
        # The globs never see desktop-plugins/<n>/*.test.mjs. Only real-browser suites
        # (esbuild + Playwright + Chromium, run by hand) may live there, named *.browser.test.mjs.
        stray = [f.relative_to(ROOT).as_posix() for f in ROOT.glob('desktop-plugins/**/*.test.mjs')
                 if f.parent.name not in ('test', 'tests') and not f.name.endswith('.browser.test.mjs')]
        self.assertEqual(stray, [], 'move these into desktop-plugins/<n>/test/ so node-tests runs them')


if __name__ == '__main__':
    unittest.main()
