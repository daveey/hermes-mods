"""Run hermetic plugin regressions against a chosen clean core checkout.

Uses the installed PM runtime for dependencies, with an optional PM test env.
Stages tests under core's tests/agent to inherit real-home isolation and guards;
removes only that freshly-created directory on exit. No production file edits.
"""
import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile

PLUGIN = Path(__file__).resolve().parent
# The plugin used to live at ~/.hermes/plugins/<name>; from a hermes-mods clone
# (symlinked into ~/.hermes/plugins) parents[1] is the clone, not the Hermes home.
INSTALLED = PLUGIN.parents[1] / 'hermes-agent'
if not INSTALLED.is_dir():
    INSTALLED = Path(os.environ.get('HERMES_AGENT_ROOT') or Path.home() / '.hermes' / 'hermes-agent')
parser = argparse.ArgumentParser()
parser.add_argument('--repo', type=Path, default=INSTALLED)
parser.add_argument('--test-site-packages', type=Path)
parser.add_argument('--without-plugin', action='store_true')
args, pytest_args = parser.parse_known_args()
repo = args.repo.resolve()
sys.path.insert(0, str(INSTALLED))
import hermes_bootstrap  # noqa: E402

if repo != INSTALLED:
    # Bootstrap provides PM dependencies; load all actual core modules from the
    # chosen checkout, not bootstrap's already-imported installed packages.
    for name, module in list(sys.modules.items()):
        if name != 'hermes_bootstrap' and str(getattr(module, '__file__', '')).startswith(str(INSTALLED) + '/'):
            del sys.modules[name]
    sys.path[:] = [p for p in sys.path if p != str(INSTALLED)]
os.environ['HERMES_PYTHON_SRC_ROOT'] = str(repo)
sys.path[:] = [p for p in sys.path if p != str(PLUGIN)]
sys.path.insert(0, str(repo))
if args.test_site_packages:
    sys.path.insert(0, str(args.test_site_packages.resolve()))
import pytest  # noqa: E402

stage = Path(tempfile.mkdtemp(prefix='fast_rate_limit_tests_', dir=repo / 'tests' / 'agent'))
try:
    shutil.copytree(PLUGIN / 'tests', stage, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    source = stage / 'source'
    source.mkdir()
    for name in ('__init__.py', 'plugin.yaml', 'compatibility.json', 'account-usage-compatibility.json'):
        shutil.copy2(PLUGIN / name, source / name)
    shutil.copy2(repo / 'tests/agent/test_credential_pool.py', stage / 'test_upstream_credential_pool.py')
    os.environ['FAST_RATE_LIMIT_PLUGIN_SOURCE'] = str(source)
    os.environ['FAST_RATE_LIMIT_TEST_DISABLE'] = '1' if args.without_plugin else '0'
    os.chdir(repo)
    targets = ([str(stage / 'test_credential_pool_routing.py'), str(stage / 'test_long_reset_pool_fallback.py')]
               if args.without_plugin else [str(stage)])
    print(f'Core checkout: {repo}; plugin enabled in isolated homes: {not args.without_plugin}', flush=True)
    result = pytest.main(targets + (pytest_args or ['-q', '--tb=short']))
finally:
    shutil.rmtree(stage)
raise SystemExit(result)
