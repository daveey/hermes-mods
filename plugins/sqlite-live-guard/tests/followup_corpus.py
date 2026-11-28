"""Concrete non-blocking points from PR 531 reviews (6093006327..6094132069).

False-positive relief is deliberately excluded: this hardening keeps all refusals.
"""
import shlex

HOME = '/home/sqlite-followup-fixture'
BODIES = {
    'pushd': 'pushd ~/.hermes; rm -rf state.db',
    'pushd_parent': 'pushd ~ && rm -rf .hermes',
    'builtin_cd': 'builtin cd ~/.hermes; rm -rf state.db',
    'pushd_after_pipeline': 'ls | pushd ~/.hermes; rm -rf state.db',
    'builtin_after_pipeline': 'ls | builtin cd ~/.hermes; rm -rf state.db',
    'env_chdir': 'env -C ~/.hermes rm -rf state.db',
    'read_pipeline': 'echo .hermes | read D; rm -rf "$D"',
    'oldpwd': 'OLDPWD=~/.hermes; cd - >/dev/full || rm -rf state.db',
    'cdpath': 'CDPATH=~ cd .hermes >/dev/full || rm -rf state.db',
    'plain_pipeline': 'ls | cd ~/.hermes; rm -rf state.db',
    'brace_left_and': 'D=~/.hermes; { D=/tmp; ls; } | head && rm -rf "$D"',
    'brace_left_semi': 'D=~/.hermes; { D=/tmp; ls; } | head; rm -rf "$D"',
    'brace_background': 'D=~/.hermes; { D=/tmp; } & rm -rf "$D"',
    'brace_right_alias': 'ls | { D=~/.hermes; rm -rf "$D"; }',
    'brace_right_cd': 'ls | { cd ~/.hermes; rm -rf state.db; }',
    'brace_right_or': 'ls | { cd ~/.hermes || exit; rm -rf state.db; }',
    'brace_right_and': 'ls | { D=~/.hermes && rm -rf "$D"; }',
    'brace_right_wildcard': 'cat x | { cd ~/.hermes && rm -rf *; }',
    'case_scope': 'D=~/.hermes; (case a in a) D=/tmp;; esac; true) && rm -rf "$D"',
    'case_pipeline': 'ls | case a in a) cd ~/.hermes; rm -rf state.db;; esac',
    'backtick': 'D=~/.hermes; echo `true; D=/tmp`; rm -rf "$D"',
    'function_body': 'D=~/.hermes; f() { D=/tmp; }; rm -rf "$D"',
    'eval_cd': 'eval "cd ~/.hermes"; rm -rf state.db',
    'backtick_negation': 'echo `! cd ~/.hermes || rm -rf state.db`',
}
REMOTE = {
    'ssh_relative': "ssh example.com 'rm -rf .hermes'",
    'ssh_failed_cd_semi': "ssh example.com 'cd /nonexistent; rm -rf .hermes'",
    'ssh_failed_cd_or': "ssh example.com 'cd /nonexistent || rm -rf .hermes'",
}
PYTHON = {
    'rename_keyword': "from os import rename; rename(src='x', dst='~/.hermes/state.db')",
    'rmtree_literal': "import shutil; shutil.rmtree('~/.hermes')",
    'relative_unlink': "from os import unlink; unlink('state.db')",
    'relative_remove': "import os; os.remove('state.db')",
    'relative_path': "from pathlib import Path; Path('state.db').unlink()",
    'relative_write': "open('state.db', 'w').write('')",
    'computed_expanduser': "import os, shutil; shutil.rmtree(os.path.expanduser('~/.hermes'))",
    'computed_pathhome': "from pathlib import Path; import shutil; shutil.rmtree(Path.home()/'.hermes')",
}

def shell_cases():
    cases = []
    for key, body in BODIES.items():
        for wrapper in ('direct', 'bash', 'dash', 'zsh', 'ssh'):
            command = body if wrapper == 'direct' else (
                'ssh example.com ' + shlex.quote(body) if wrapper == 'ssh' else
                wrapper + ' -c ' + shlex.quote(body))
            cases.append({'id': key + '/' + wrapper, 'command': command, 'cwd': HOME})
            if key == 'pushd_parent':
                cases.append({'id': key + '/cache/' + wrapper, 'command': command,
                              'cwd': HOME + '/.hermes/cache'})
    cases.extend({'id': key, 'command': command, 'cwd': HOME} for key, command in REMOTE.items())
    return cases
