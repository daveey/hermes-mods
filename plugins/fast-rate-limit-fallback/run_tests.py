"""Run regressions in a disposable snapshot of the chosen core checkout's HEAD.

The parent owns cleanup; bootstrap, core guards and pytest run only in a child.
No tests or bytecode are written into the installed core checkout.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

PLUGIN = Path(__file__).resolve().parent
INSTALLED = PLUGIN.parents[1] / 'hermes-agent'
if not INSTALLED.is_dir():
    INSTALLED = Path(os.environ.get('HERMES_AGENT_ROOT') or Path.home() / '.hermes' / 'hermes-agent')

# Dependencies come from the installed PM runtime, but core imports must resolve
# to the snapshot even if bootstrap imported installed core packages already.
WORKER = '''
import os, sys
sys.dont_write_bytecode = True
installed, repo, site, disabled, *targets = sys.argv[1:]
sys.path.insert(0, installed)
import hermes_bootstrap
for name, module in list(sys.modules.items()):
    if name != 'hermes_bootstrap' and str(getattr(module, '__file__', '')).startswith(installed + '/'):
        del sys.modules[name]
sys.path[:] = [p for p in sys.path if p != installed]
sys.path.insert(0, repo)
if site:
    sys.path.insert(0, site)
os.environ['HERMES_PYTHON_SRC_ROOT'] = repo
os.environ['FAST_RATE_LIMIT_TEST_DISABLE'] = disabled
os.chdir(repo)
import pytest
raise SystemExit(pytest.main(targets))
'''


def stage_suite(repo, root):
    """Archive HEAD, not untracked files, environments or prior leaked stages."""
    archive = root / 'core.tar'
    subprocess.run(['git', '-C', str(repo), 'archive', '--format=tar',
                    f'--output={archive}', 'HEAD'], check=True)
    core = root / 'core'
    core.mkdir()
    with tarfile.open(archive) as bundle:
        bundle.extractall(core, filter='data')
    archive.unlink()
    stage = core / 'tests' / 'agent' / 'fast_rate_limit_tests'
    shutil.copytree(PLUGIN / 'tests', stage, ignore=shutil.ignore_patterns('__pycache__'))
    source = stage / 'source'
    source.mkdir()
    for name in ('__init__.py', 'plugin.yaml', 'compatibility.json', 'account-usage-compatibility.json'):
        shutil.copy2(PLUGIN / name, source / name)
    shutil.copy2(core / 'tests/agent/test_credential_pool.py', stage / 'test_upstream_credential_pool.py')
    return core, stage, source


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=INSTALLED,
                        help='Core checkout whose committed HEAD is tested (never modified)')
    parser.add_argument('--test-site-packages', type=Path)
    parser.add_argument('--without-plugin', action='store_true')
    args, pytest_args = parser.parse_known_args(argv)
    repo = args.repo.resolve()
    site = str(args.test_site_packages.resolve()) if args.test_site_packages else ''
    # Keep the lifetime outside the child: even collection failure, guard
    # installation, SystemExit or KeyboardInterrupt cannot monkeypatch cleanup.
    with tempfile.TemporaryDirectory(prefix='fast_rate_limit_tests_') as temporary:
        core, stage, source = stage_suite(repo, Path(temporary))
        targets = ([str(stage / 'test_credential_pool_routing.py'), str(stage / 'test_long_reset_pool_fallback.py')]
                   if args.without_plugin else [str(stage)])
        print(f'Core checkout HEAD: {repo}; snapshot: {core}; '
              f'plugin enabled in isolated homes: {not args.without_plugin}', flush=True)
        return subprocess.run([sys.executable, '-I', '-c', WORKER,
                               str(INSTALLED.resolve()), str(core), site,
                               '1' if args.without_plugin else '0',
                               *targets, *(pytest_args or ['-q', '--tb=short'])]).returncode


if __name__ == '__main__':
    raise SystemExit(main())
