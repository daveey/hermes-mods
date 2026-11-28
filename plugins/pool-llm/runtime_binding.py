"""Read-only managed-launcher binding; never execute runtime discovery.

Git identity is provenance, not independent approval. Core/PM/dependencies remain
managed-install trust and must be audited separately from this custom launcher.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess


class BindingError(ValueError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    data = path.read_bytes()
    if len(data) > 2 * 1024 * 1024:
        raise BindingError('binding source too large')
    return data


def shell(command):
    return '#!/bin/sh\nexec ' + shlex.join(command) + ' "$@"\n'


def parse_shell(path):
    text = read(path).decode('utf-8')
    if not text.startswith('#!/bin/sh\nexec ') or not text.endswith(' "$@"\n'):
        raise BindingError('noncanonical managed launcher')
    command = shlex.split(text[len('#!/bin/sh\nexec '):-len(' "$@"\n')])
    if not command or text != shell(command):
        raise BindingError('extra launcher prelude or shell syntax')
    return command


def evaluate(node, names):
    """Tiny data-only renderer language, not eval/compile/import of core code."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int)):
        return node.value
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    if isinstance(node, ast.Tuple):
        return tuple(evaluate(x, names) for x in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = evaluate(node.left, names), evaluate(node.right, names)
        if type(a) is str and type(b) is str:
            return a + b
    if isinstance(node, ast.JoinedStr):
        return ''.join(str(evaluate(x, names)) for x in node.values)
    if isinstance(node, ast.FormattedValue) and node.format_spec is None and node.conversion in (-1, 114):
        value = evaluate(node.value, names)
        return repr(value) if node.conversion == 114 else str(value)
    if isinstance(node, ast.Call) and not node.keywords:
        args = [evaluate(x, names) for x in node.args]
        if isinstance(node.func, ast.Name) and node.func.id == 'repr' and len(args) == 1:
            return repr(args[0])
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'replace' and len(args) == 2:
            value = evaluate(node.func.value, names)
            if type(value) is str and all(type(x) is str for x in args):
                return value.replace(*args)
    raise BindingError('unsupported managed renderer expression')


def render(template, recovery, root):
    names = {'root': str(root), 'module': 'hermes_cli.main', 'func': 'main'}
    constants = {'INTERRUPTED_PULL_MARKER', 'RECOVERY_CLOSURE', 'RECOVERY_CLOSURE_DIR',
                 'RECOVERY_CLOSURE_INIT', 'RECOVERY_CLOSURE_MANIFEST'}
    for node in ast.parse(recovery).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in constants:
                names[node.targets[0].id] = ast.literal_eval(node.value)
    tree = ast.parse(template)
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            names[node.targets[0].id] = evaluate(node.value, names)
        elif isinstance(node, ast.FunctionDef) and node.name == 'render':
            for statement in node.body:
                if isinstance(statement, ast.ImportFrom) and statement.module == 'hermes_cli._early_recovery':
                    if any(x.name not in constants or x.asname for x in statement.names):
                        raise BindingError('unsupported renderer import')
                elif isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                    names[statement.targets[0].id] = evaluate(statement.value, names)
                elif isinstance(statement, ast.Return):
                    return evaluate(statement.value, names)
                else:
                    raise BindingError('unsupported renderer statement')
    raise BindingError('managed renderer missing')


def git(root, *args):
    # Fixed system Git, no hooks, pager, lazy network fetch, or inherited routing.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_NO_LAZY_FETCH='1', GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1',
               GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS='0')
    done = subprocess.run(['/usr/bin/git', '--no-replace-objects', '-C', str(root), *args],
                          env=env, capture_output=True, timeout=15, stdin=subprocess.DEVNULL)
    if done.returncode:
        raise BindingError('managed Git provenance unavailable')
    return done.stdout


def resolve(home=None, *, _with_command=False):
    """Read bytes/Git objects only: no bootstrap, provider, repair or token reads."""
    convenience = (Path.home() if home is None else Path(home)) / '.local/bin/hermes'
    forwarding = parse_shell(convenience)
    if len(forwarding) != 1 or not Path(forwarding[0]).is_absolute():
        raise BindingError('expected managed convenience forwarder')
    launcher = Path(forwarding[0])
    if launcher.parts[-3:] != ('.hermes', 'bin', 'hermes'):
        raise BindingError('unexpected managed launcher selection')
    root = launcher.parents[2].resolve(strict=True)
    commit = git(root, 'rev-parse', 'HEAD').decode().strip()
    if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', commit):
        raise BindingError('invalid managed commit')
    records = {}
    sources = {}
    for relative in ('hermes_cli/_launch_template.py', 'hermes_cli/_early_recovery.py', 'hermes_cli/_launchers.py'):
        source = git(root, 'show', commit + ':' + relative)
        if read(root / relative) != source:
            raise BindingError('managed publication source differs from Git')
        sources[relative] = source
        records[relative] = {'blob': git(root, 'rev-parse', commit + ':' + relative).decode().strip(),
                             'sha256': sha(source)}
    command = parse_shell(launcher)
    if len(command) != 4 or command[1:3] != ['-I', '-c'] or not Path(command[0]).is_absolute():
        raise BindingError('unexpected managed interpreter flags')
    expected = render(sources['hermes_cli/_launch_template.py'], sources['hermes_cli/_early_recovery.py'], root)
    if command[3] != expected:
        raise BindingError('managed launcher differs from complete committed template')
    python = Path(command[0])
    if not python.is_file() or not os.access(python, os.X_OK):
        raise BindingError('managed interpreter unavailable')
    # Persisted POSIX publishers select the Python package recorded by PM, not
    # an arbitrary absolute executable substituted into an otherwise valid shim.
    store = python.parents[2]
    facts_path = store / 'facts.json'
    facts_bytes = read(facts_path)
    facts = json.loads(facts_bytes)
    entry = facts.get('packages', {}).get('python', {}).get('entry')
    if not isinstance(entry, str) or not entry or Path(entry).is_absolute() or '..' in Path(entry).parts:
        raise BindingError('invalid managed Python selection')
    if python != store / entry / 'bin/python3':
        raise BindingError('interpreter differs from managed Python selection')
    receipt = {'version': 1, 'trust': 'managed-core-and-dependencies-not-plugin-approval',
            'python_facts': str(facts_path), 'python_facts_sha256': sha(facts_bytes),
            'convenience': str(convenience), 'launcher': str(launcher), 'core': str(root),
            'commit': commit, 'sources': records, 'launcher_sha256': sha(read(launcher)),
            'python': str(python), 'python_sha256': sha(python.read_bytes())}
    return (receipt, command) if _with_command else receipt


def command(module, argv):
    bound, selected = resolve(_with_command=True)
    # Transform only the known terminal entry point, after comparing the WHOLE
    # program with its committed renderer. Never accept a MARK-containing prelude.
    tail = ("from hermes_cli.main import main\n"
            "sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
            "sys.exit(main())\n")
    if not selected[3].endswith(tail):
        raise BindingError('unsupported managed terminal entry point')
    body = ("import runpy\n" + f"sys.argv = [{str(module)!r}] + sys.argv[1:]\n"
            + f"runpy.run_path({str(module)!r}, run_name='__main__')\n")
    return [selected[0], '-I', '-c', selected[3][:-len(tail)] + body, *argv]
