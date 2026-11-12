"""AWS invocation detection through wrappers/compound syntax (RUN16 alignment).

`_shell_invocations` skipped prefixes by name only: `sudo -u x aws ...`,
`if aws ...; then`, `{ aws ...; }`, `timeout 5 aws ...` etc. were not seen as AWS
calls, so a denied operation could be repeated through a wrapper. Synthetic
text only: nothing here is executed and no AWS call is made.
"""
import unittest

import guard as g

OP = ('ec2', g._norm('terminate-instances'))
CMD = 'aws ec2 terminate-instances --instance-ids i-0123456789abcdef0'

HOLD = [
    f'sudo -u deploy {CMD}', f'sudo -E -u deploy -- {CMD}', f'sudo -iu root {CMD}', f'doas {CMD}',
    f'env -i PATH=/usr/bin {CMD}', f'env -u HOME {CMD}', f'env --chdir=/tmp {CMD}',
    f'command -p {CMD}', f'exec -a x {CMD}', f'nohup {CMD}', f'nice -n 5 {CMD}', f'ionice -c3 {CMD}',
    f'timeout 30 {CMD}', f'timeout -s KILL 30 {CMD}', f'stdbuf -oL {CMD}', f'setsid {CMD}', f'time {CMD}',
    f'echo i-0123456789abcdef0 | xargs {CMD}', f'flock /tmp/l {CMD}', f'unbuffer {CMD}', f'builtin command {CMD}',
    f'if {CMD}; then :; fi', f'if true; then {CMD}; fi', f'while true; do {CMD}; done', f'until false; do {CMD}; done',
    f'{{ {CMD}; }}', f'( {CMD} )', f'! {CMD}', f'f() {{ {CMD}; }}; f', f'function f {{ {CMD}; }}; f',
    f'true && {CMD}', f'false || {CMD}', f'for i in 1; do {CMD}; done', f'case x in x) {CMD};; esac',
    f'eval "{CMD}"', f"eval '{CMD}'", f'sudo timeout 5 nohup {CMD}', f'/usr/local/bin/{CMD}',
    f"sudo -u x bash -c '{CMD}'", f"timeout 5 sh -c '{CMD}'",
]
# Dynamic command words naming aws cannot be resolved: held as unknown AWS.
UNKNOWN = ['A=aws; $A ec2 terminate-instances --instance-ids i-0123456789abcdef0',
           '"$(command -v aws)" ec2 terminate-instances --instance-ids i-0123456789abcdef0',
           '${AWS:-aws} ec2 terminate-instances --instance-ids i-0123456789abcdef0',
           # REVIEW-AWS B1: wrappers must not hide a dynamic wrapped command word.
           'C=aws; env "$C" ec2 terminate-instances --instance-ids i-0123456789abcdef0',
           'C=aws; sudo "$C" ec2 terminate-instances --instance-ids i-0123456789abcdef0',
           'C=aws; timeout 5 $C ec2 terminate-instances --instance-ids i-0123456789abcdef0',
           f"env -S '{CMD}'", f"env --split-string='{CMD}'", f"env -S '{CMD}' extra"]
CLEAN = ['echo aws is great', 'grep -r aws .', 'cat ~/.aws/config', 'sudo -u aws ls', 'ls -la', 'timeout 5 make test',
         'if true; then echo ok; fi', 'pip install awscli-local', 'echo "$HOME"']


class AwsWrappers(unittest.TestCase):
    def test_wrapped_invocations_detected(self):
        for text in HOLD:
            with self.subTest(text):
                self.assertIn(OP, g._calls('shell', text))

    def test_dynamic_command_word_unknown(self):
        for text in UNKNOWN:
            with self.subTest(text):
                calls = g._calls('shell', text)
                self.assertTrue(OP in calls or ('UNKNOWN', 'UNKNOWN') in calls, calls)

    def test_non_aws_commands_have_no_calls(self):
        for text in CLEAN:
            with self.subTest(text):
                self.assertNotIn(('UNKNOWN', 'UNKNOWN'), g._calls('shell', text))
                self.assertFalse(any(svc == 'ec2' for svc, _ in g._calls('shell', text)))

    def test_python_argv_wrappers(self):
        for argv in (['aws', 'ec2', 'terminate-instances'], ['sudo', '-u', 'x', 'aws', 'ec2', 'terminate-instances'],
                     ['timeout', '5', 'aws', 'ec2', 'terminate-instances'], ['env', '-i', 'aws', 'ec2', 'terminate-instances']):
            with self.subTest(argv):
                code = f'import subprocess\nsubprocess.run({argv!r})'
                calls = g._calls('python', code)
                self.assertTrue(OP in calls or ('UNKNOWN', 'UNKNOWN') in calls, calls)
        for code in (f"import os\nos.system({CMD!r})", f"from subprocess import check_output\ncheck_output({CMD!r}, shell=True)",
                     "import os\nos.execvp('aws', ['aws', 'ec2', 'terminate-instances'])",
                     "import subprocess\ncmd = ['aws', 'ec2', 'terminate-instances']\nsubprocess.run(cmd)",
                     # REVIEW-AWS B2: variadic launchers; B3: multiline/aliased imports.
                     "import os\nos.execlp('aws', 'aws', 'ec2', 'terminate-instances')",
                     "import os\nos.execl('/usr/bin/aws', 'aws', 'ec2', 'terminate-instances')",
                     "import os\nos.spawnlp(os.P_WAIT, 'aws', 'aws', 'ec2', 'terminate-instances')",
                     "import os\nos.execve('/usr/bin/aws', ['aws', 'ec2', 'terminate-instances'], {})",
                     "import asyncio\nasync def m():\n    p = await asyncio.create_subprocess_exec('aws', 'ec2', 'terminate-instances')\nasyncio.run(m())",
                     "from subprocess import (\n    run,\n)\nrun(['aws', 'ec2', 'terminate-instances'])",
                     "from subprocess import run as go\ngo(['aws', 'ec2', 'terminate-instances'])",
                     "import subprocess as s\ns.run(['aws', 'ec2', 'terminate-instances'])",
                     "import subprocess\nsubprocess.run(['x', 'ec2', 'terminate-instances'], executable='/usr/bin/aws')",
                     # REVIEW-AWS-CORRECTION F1: the path argument is the program; F2: starred/empty argv.
                     "import os\nos.execv('/usr/bin/aws', ['x', 'ec2', 'terminate-instances'])",
                     "import os\nos.execl('/usr/bin/aws', 'x', 'ec2', 'terminate-instances')",
                     "import os\nos.execvp('aws', ['label', 'ec2', 'terminate-instances'])",
                     "import os\nos.posix_spawn('/usr/bin/aws', ['x', 'ec2', 'terminate-instances'], {})",
                     "import os\nos.spawnv(os.P_WAIT, '/usr/bin/aws', ['x', 'ec2', 'terminate-instances'])",
                     "import os\nos.execl(*a)  # aws", "import os\nos.execv(*a)  # aws", "import os\nos.posix_spawn(*a)  # aws",
                     "import os\nos.spawnvp(os.P_WAIT)  # aws", "import os\nos.execv(p, argv)  # aws",
                     "import subprocess\nsubprocess.run(*a)  # aws", "import subprocess\nsubprocess.run(**kw)  # aws",
                     # REVIEW-AWS-FINAL F3: starred argv elements; F4: asyncio executable override.
                     "import os\nos.execv('/usr/bin/aws', [*['x', 'ec2'], 'terminate-instances'])",
                     "import os\np = ['x', 'ec2']\nos.posix_spawnp('aws', (*p, 'terminate-instances'), {})",
                     "import os\np = ['x', 'ec2']\nos.spawnvpe(os.P_WAIT, 'aws', [*p, 'terminate-instances'], {})",
                     "import subprocess\np = ['aws']\nsubprocess.run([*p, 'ec2', 'terminate-instances'])",
                     "import asyncio\nasyncio.run(asyncio.create_subprocess_exec('x', 'ec2', 'terminate-instances', executable='/usr/bin/aws'))",
                     "import asyncio\ne = '/usr/bin/aws'\nasyncio.run(asyncio.create_subprocess_exec('x', 'ec2', 'terminate-instances', executable=e))",
                     # REVIEW-AWS-FINAL2 F5: an import in another scope must not erase a launcher binding.
                     "import subprocess\ndef unused():\n    import pty as subprocess\nsubprocess.run(['aws', 'ec2', 'terminate-instances'])",
                     "import subprocess as x\ndef unused():\n    import os as x\nx.run(['aws', 'ec2', 'terminate-instances'])",
                     "import os as x\nclass Unused:\n    import subprocess as x\nx.execv('/usr/bin/aws', ['x', 'ec2', 'terminate-instances'])",
                     "from os import execl as go\ndef unused():\n    from subprocess import run as go\ngo('/usr/bin/aws', 'x', 'ec2', 'terminate-instances')",
                     # REVIEW-AWS-FINAL3 F6: wildcard imports.
                     "from subprocess import *\nrun(['aws', 'ec2', 'terminate-instances'])",
                     "from os import *\nexecv('/usr/bin/aws', ['x', 'ec2', 'terminate-instances'])",
                     "from asyncio import *\nrun(create_subprocess_exec('aws', 'ec2', 'terminate-instances'))",
                     "def f():\n    from subprocess import *\n    check_output('aws ec2 terminate-instances', shell=True)",
                     # Dynamic module/attribute access in code that names aws.
                     "__import__('subprocess').run(['aws', 'ec2', 'terminate-instances'])",
                     "import importlib\nimportlib.import_module('subprocess').run(['aws', 'ec2', 'terminate-instances'])",
                     "import subprocess\ngetattr(subprocess, 'run')(['aws', 'ec2', 'terminate-instances'])",
                     "import sys\nsys.modules['subprocess'].run(['aws', 'ec2', 'terminate-instances'])",
                     # REVIEW-AWS-FINAL4 F7: submodules, re-exports, posix/nt, loop methods.
                     "from asyncio.subprocess import create_subprocess_exec as c\nc('aws', 'ec2', 'terminate-instances')",
                     "from asyncio.subprocess import *\ncreate_subprocess_exec('aws', 'ec2', 'terminate-instances')",
                     "from asyncio import subprocess as s\ns.create_subprocess_shell('aws ec2 terminate-instances')",
                     "import asyncio.subprocess as s\ns.create_subprocess_exec('aws', 'ec2', 'terminate-instances')",
                     "import subprocess\nsubprocess.os.system('aws ec2 terminate-instances')",
                     "import pty\npty.os.system('aws ec2 terminate-instances')",
                     "from subprocess import os as o\no.system('aws ec2 terminate-instances')",
                     "import asyncio\nasyncio.subprocess.subprocess.run(['aws', 'ec2', 'terminate-instances'])",
                     "import posix\nposix.system('aws ec2 terminate-instances')",
                     "from posix import system\nsystem('aws ec2 terminate-instances')",
                     "import asyncio\nasyncio.get_event_loop().subprocess_exec(asyncio.SubprocessProtocol, 'aws', 'ec2', 'terminate-instances')",
                     "import asyncio\nloop.subprocess_shell(asyncio.SubprocessProtocol, 'aws ec2 terminate-instances')",
                     # REVIEW-AWS-FINAL5 F8: re-exported launcher module bound by from-import, generic names.
                     "from subprocess import os as o\no.popen('aws ec2 terminate-instances')",
                     "from pty import os as o\no.popen('aws ec2 terminate-instances')",
                     "from shutil import os\nos.popen('aws ec2 terminate-instances')",
                     "from asyncio.subprocess import subprocess as s\ns.run(['aws', 'ec2', 'terminate-instances'])",
                     "from asyncio import subprocess as s\ns.subprocess.call(['aws', 'ec2', 'terminate-instances'])",
                     # REVIEW-AWS-FINAL6 F9: relative imports (package unknown) in aws-spelling code.
                     "__package__ = 'asyncio'\nfrom .subprocess import subprocess as s\ns.run(['aws', 'ec2', 'terminate-instances'])",
                     "from . import os as o\no.popen('aws ec2 terminate-instances')",
                     "from .subprocess import create_subprocess_exec\ncreate_subprocess_exec('aws', 'ec2', 'terminate-instances')"):
            with self.subTest(code):
                calls = g._calls('python', code)
                self.assertTrue(OP in calls or ('UNKNOWN', 'UNKNOWN') in calls, calls)
        for code in ("import subprocess\nsubprocess.run(['ls', '-la'])", "import asyncio\nasyncio.run(main())",
                     "import subprocess\nsubprocess.run(cmd)",
                     # REVIEW-AWS P1: asyncio.run is not a process launcher.
                     "import asyncio\n# aws docs\nasyncio.run(main())",
                     "import subprocess\n# aws\nsubprocess.run(['ls'], capture_output=True, timeout=5)",
                     "import os\nos.execv('/bin/ls', ['ls', '-la'])", "import os\nos.execl(*a)",
                     "import os\nos.posix_spawn('/bin/true', ['true'], {})",
                     "import platform\n# aws\nprint(platform.system())", "# aws\napp.run(debug=True)\nflask.run()",
                     "# aws\nimport unittest\nmock.call(x)\nself.call(y)"):
            with self.subTest(code):
                self.assertEqual(g._calls('python', code), [])


if __name__ == '__main__':
    unittest.main()
