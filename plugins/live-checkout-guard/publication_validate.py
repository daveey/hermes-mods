"""Plugin-owned publication hooks: privacy plus a conservative doc-only fast path.

Never execute programs/configuration from the fetched repository. Skills with any
scripts/tests are held for the ordinary repository suite/PR flow instead.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent


def git(*args):
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null',
                                    '-c', 'core.fsmonitor=false', *args], stderr=subprocess.PIPE)


def validate_scope(base, head=None):
    span = [base, head] if head else ['--cached', base]
    names = git('diff', '--no-ext-diff', '--no-textconv', '--no-renames',
                '--name-only', '-z', *span).decode().rstrip('\0').split('\0')
    tree = git('ls-tree', '-rz', head).split(b'\0') if head else git('ls-files', '--stage', '-z').split(b'\0')
    entries = {}
    for row in tree:
        if row:
            meta, name = row.split(b'\t', 1)
            entries[name.decode()] = meta.split()[0]
    owners = set()
    for name in names:
        p = PurePosixPath(name)
        if (not name or p.parts[0] != 'skills' or len(p.parts) < 3 or
                '..' in p.parts or any(x.startswith('.') for x in p.parts) or '\\' in name):
            raise ValueError('publication requires skill documentation only')
        support = next((i for i, x in enumerate(p.parts) if x in {'references', 'templates', 'assets'}), None)
        if support is None:
            if p.name != 'SKILL.md':
                raise ValueError('publication requires Markdown skill documentation')
            owner = p.parent.as_posix()
        else:
            if support < 2 or p.suffix != '.md':
                raise ValueError('publication requires Markdown skill documentation')
            owner = '/'.join(p.parts[:support])
        if name in entries and entries[name] != b'100644':
            raise ValueError('publication requires regular non-executable documents')
        owners.add(owner)
    # Superset of the repository doc-fast-path hold: do not run fetched suite code,
    # or guess whether a script/test depends on this document.
    for owner in owners:
        prefix = owner + '/'
        for name in entries:
            if name.startswith(prefix):
                parts = PurePosixPath(name[len(prefix):]).parts
                if any(x in {'tests', 'scripts'} or x.startswith(('test_', 'tests_', 'test.', 'tests.'))
                       or re.search(r'[._-](?:test|spec)\.', x) for x in parts):
                    raise ValueError('skill has scripts/tests; use the normal reviewed publication flow')
    return names


def privacy():
    candidates = [os.environ.get('HERMES_MODS_DENYLIST', ''),
                  str(Path.home() / 'code/hermes/mods-denylist.txt'),
                  str(Path.home() / '.hermes/repos/hermes-state/mods-denylist.txt')]
    deny = next((Path(p) for p in candidates if p and Path(p).is_file()), None)
    if deny is None:
        raise ValueError('privacy check(s) cannot run: denylist')
    try:
        subprocess.run(['gitleaks', 'git', '--pre-commit', '--staged', '--no-banner', '--redact',
                        '--ignore-gitleaks-allow', '--gitleaks-ignore-path', str(HERE / 'publication.gitleaksignore'),
                        '-c', str(HERE / 'publication.gitleaks.toml')], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    except FileNotFoundError:
        raise ValueError('privacy check(s) cannot run: gitleaks') from None
    except subprocess.CalledProcessError:
        raise ValueError('gitleaks refused publication') from None
    patterns = '\n'.join(x for x in deny.read_text().splitlines() if x.strip() and not x.lstrip().startswith('#'))
    if patterns:
        diff = git('diff', '--cached', '-U0', '--no-renames', '--diff-filter=d',
                   '--no-color', '--no-ext-diff', '--no-textconv')
        added = b'\n'.join(x for x in diff.splitlines() if x.startswith(b'+'))
        with tempfile.NamedTemporaryFile(mode='w') as f:
            f.write(patterns + '\n')
            f.flush()
            result = subprocess.run(['grep', '-iE', '-f', f.name], input=added,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        if result.returncode != 1:
            raise ValueError('private denylist refused publication')


def main():
    try:
        if sys.argv[1:] == ['pre-commit']:
            validate_scope('HEAD')
            privacy()
        elif sys.argv[1:] == ['pre-push']:
            rows = sys.stdin.read().splitlines()
            if len(rows) != 1:
                raise ValueError('publication requires one main update')
            local_ref, head, remote_ref, base = rows[0].split()
            if remote_ref != 'refs/heads/main' or set(base) == {'0'} or set(head) == {'0'}:
                raise ValueError('publication requires an existing main update')
            validate_scope(base, head)
        else:
            raise ValueError('unknown publication hook')
    except Exception as exc:
        print('publication held: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
