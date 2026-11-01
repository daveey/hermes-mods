"""Shell command strings must quote the interpreter path.

The macOS production interpreter is ".../Hermes Python.app/Contents/MacOS/python3.14". Interpolated
unquoted into a shell command string, sys.executable makes the shell run ".../Hermes", which exits
127. On the hub (no space in the path) such a test passes; on the Mac two coach exec-and-wait
fixtures never ran the interpreter at all.
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# `{sys.executable}` directly followed by an option is a command line; a message ("missing in
# {sys.executable}: ...") is not.
UNQUOTED = re.compile(r"\{sys\.executable\}\s+-")


def tracked_python_files() -> list[Path]:
    try:
        out = subprocess.run(['git', 'ls-files', '-z', '--', '*.py'], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout
        return [ROOT / name for name in out.split('\0') if name]
    except (OSError, subprocess.CalledProcessError):  # not a git checkout (e.g. an unpacked archive)
        return [p for d in ('plugins', 'tests', 'scripts') for p in (ROOT / d).rglob('*.py')]


class ShellQuotedInterpreter(unittest.TestCase):
    def test_no_unquoted_interpreter_in_shell_command_strings(self):
        hits = [f'{path.relative_to(ROOT)}:{n}: {line.strip()}'
                for path in tracked_python_files() if path.is_file()
                for n, line in enumerate(path.read_text(errors='replace').splitlines(), 1)
                if UNQUOTED.search(line)]
        self.assertEqual(hits, [], 'quote it: shlex.quote(sys.executable)\n' + '\n'.join(hits))

    def test_pattern_flags_unquoted_command_only(self):
        exe = '{sys.executable}'
        self.assertTrue(UNQUOTED.search('f"exec ' + exe + " -c 'pass'\""))
        self.assertTrue(UNQUOTED.search('f"' + exe + " -c 'print(1)'\""))
        self.assertFalse(UNQUOTED.search('f"exec {shlex.quote(sys.executable)} -c \'pass\'"'))
        self.assertFalse(UNQUOTED.search('f"dependencies missing in ' + exe + ': {exc}"'))


if __name__ == '__main__':
    unittest.main()
