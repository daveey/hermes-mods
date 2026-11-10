"""Refuse stop-the-world py-spy attachment in agent-issued commands.

A bounded duration is not enough: ptrace sampling can fall behind while keeping
threads stopped. Nonblocking reads trade snapshot consistency for availability.
This is an accidental-command guard, not a shell/Python security boundary.
"""
import ast
import re
import shlex
from pathlib import PurePosixPath

MESSAGE = (
    'Attached py-spy record/top must use --nonblocking. Blocking sampling suspends '
    'the target and can freeze a live backend for minutes even with --duration. '
    'Use a bounded --nonblocking recording; for consistent blocking samples, '
    'launch an isolated fixture under py-spy instead of attaching to a PID.'
)


def _unsafe(command, depth=0):
    if depth > 6 or not isinstance(command, str):
        return False
    # Heredoc data is not a new simple command (dynamic shell expansion is out of scope).
    lines, delimiter = [], None
    for line in command.splitlines():
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        lines.append(line)
        match = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", line)
        if match:
            delimiter = match.group(1)
    command = '\n'.join(lines)
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=';&|\n<>')
        lex.whitespace = ' \t\r'
        lex.whitespace_split = True
        words = list(lex)
    except ValueError:
        return False
    segments, segment = [], []
    for word in words:
        if word and all(c in ';&|\n' for c in word):
            segments.append(segment)
            segment = []
        else:
            segment.append(word)
    segments.append(segment)
    for words in segments:
        # Remove redirection operators and their filenames before interpreting options.
        clean, skip = [], False
        for word in words:
            if skip:
                skip = False
                continue
            if word and all(c in '<>' for c in word):
                skip = True
                continue
            clean.append(word)
        words = clean
        # Only command positions, never arbitrary argv/data. Common transparent wrappers.
        while words:
            first = PurePosixPath(words[0]).name
            if '=' in first and not first.startswith('-'):
                words = words[1:]
                continue
            if first not in {'sudo', 'env', 'nice', 'command', 'exec'}:
                break
            wrapper = words.pop(0)
            while words and words[0].startswith('-'):
                opt = words.pop(0)
                if opt in {'-u', '-g', '-n' if wrapper == 'nice' else '-U'} and words:
                    words.pop(0)
        if not words:
            continue
        first = PurePosixPath(words[0]).name
        if first in {'ssh', 'bash', 'sh', 'zsh'}:
            if any(_unsafe(w, depth + 1) for w in words[1:] if 'py-spy' in w and ' ' in w):
                return True
        if first != 'py-spy':
            continue
        args = words[1:]
        # A new workload after -- is not an attached target; do not inspect its argv.
        args = args[:args.index('--')] if '--' in args else args
        if not args or args[0] not in {'record', 'top'} or '--help' in args or '-h' in args:
            continue
        attached = any(a in {'--pid', '-p'} or a.startswith('--pid=') or
                       (a.startswith('-p') and a[2:].isdigit()) for a in args)
        if attached and '--nonblocking' not in args:
            return True
    return False


def blocked(tool_name, args):
    if not isinstance(args, dict):
        return False
    if tool_name == 'terminal':
        return _unsafe(args.get('command', ''))
    if tool_name != 'execute_code':
        return False
    try:
        tree = ast.parse(args.get('code', ''))
    except (SyntaxError, TypeError, ValueError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, 'attr', '')
        if name != 'terminal':
            continue
        command = next((k.value for k in node.keywords if k.arg == 'command'),
                       node.args[0] if node.args else None)
        if isinstance(command, ast.Constant) and _unsafe(command.value):
            return True
    return False
