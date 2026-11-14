"""Keep the desktop Node suites in CI without a private desktop checkout."""
import re
import unittest
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def ci_node_globs(text):
    """The file globs node-tests passes to `node --test`, read from the workflow itself."""
    runs = re.findall(r'^\s*node --test (.+)$', text, re.M)
    if len(runs) != 1:
        raise AssertionError(f'expected one `node --test` line in the workflow, found {len(runs)}')
    return [arg for arg in runs[0].split() if not arg.startswith('-')]


def segment_collects(glob_segment, path_segment):
    """bash (no dotglob): a leading '.' must be matched by a literal leading '.' in the pattern."""
    if path_segment.startswith('.') and not glob_segment.startswith('.'):
        return False
    return fnmatchcase(path_segment, glob_segment)


def glob_collects(glob, path):
    """True when the shell expands `glob` to `path`: same depth, each segment matched ('*' never crosses '/')."""
    parts, pattern = PurePosixPath(path).parts, PurePosixPath(glob).parts
    return len(parts) == len(pattern) and all(segment_collects(g, p) for p, g in zip(parts, pattern))


def uncollected_node_tests(paths, globs):
    """*.test.mjs paths node-tests would never run. Only hand-run *.browser.test.mjs suites are exempt."""
    return [p for p in paths
            if not p.endswith('.browser.test.mjs') and not any(glob_collects(g, p) for g in globs)]


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

    def test_no_node_suite_hides_outside_the_ci_globs(self):
        # A *.test.mjs is collected only at exactly desktop-plugins/<n>/test(s)/ or plugins/<n>/tests/:
        # not at a plugin root, not in a nested x/test/ dir. Only real-browser suites (esbuild +
        # Playwright + Chromium, run by hand) may live elsewhere, named *.browser.test.mjs.
        found = sorted(f.relative_to(ROOT).as_posix()
                       for pattern in ('desktop-plugins/**/*.test.mjs', 'plugins/**/*.test.mjs')
                       for f in ROOT.glob(pattern) if 'node_modules' not in f.parts)
        self.assertIn('desktop-plugins/user-steps/test/decision-queue.test.mjs', found)
        self.assertEqual(uncollected_node_tests(found, ci_node_globs(self.text)), [],
                         'move these into desktop-plugins/<n>/test/ so node-tests runs them')

    def test_guard_globs_come_from_the_workflow(self):
        self.assertEqual(ci_node_globs(self.text), [
            'desktop-plugins/*/test/*.mjs', 'desktop-plugins/*/tests/*.test.mjs', 'plugins/*/tests/*.test.mjs'])

    def test_guard_rejects_files_the_ci_globs_miss(self):
        collected = ['desktop-plugins/n/test/a.test.mjs', 'desktop-plugins/n/tests/a.test.mjs',
                     'plugins/n/tests/a.test.mjs', 'desktop-plugins/n/a.browser.test.mjs']
        missed = ['desktop-plugins/n/x/test/a.test.mjs',   # nested test/ dir: parent is named test, CI skips it
                  'desktop-plugins/n/x/tests/a.test.mjs',
                  'desktop-plugins/n/test/sub/a.test.mjs',
                  'desktop-plugins/n/a.test.mjs',          # plugin root
                  'desktop-plugins/test/a.test.mjs',       # too shallow
                  'plugins/n/x/tests/a.test.mjs',
                  'plugins/n/test/a.test.mjs']
        self.assertEqual(uncollected_node_tests(collected + missed, ci_node_globs(self.text)), missed)

    def test_guard_rejects_dot_prefixed_segments_the_shell_skips(self):
        # bash's `*` (no dotglob) never matches a leading '.', while fnmatch's does.
        missed = ['desktop-plugins/.x/test/a.test.mjs',    # dot-prefixed plugin dir
                  'desktop-plugins/p/test/.a.test.mjs',    # dot-prefixed test file
                  'desktop-plugins/p/tests/.a.test.mjs',
                  'plugins/.x/tests/a.test.mjs']
        self.assertEqual(uncollected_node_tests(missed, ci_node_globs(self.text)), missed)
        self.assertTrue(glob_collects('desktop-plugins/*/.hidden/*.mjs', 'desktop-plugins/p/.hidden/a.mjs'))
        self.assertFalse(glob_collects('desktop-plugins/?x/test/*.mjs', 'desktop-plugins/.x/test/a.mjs'))


if __name__ == '__main__':
    unittest.main()
