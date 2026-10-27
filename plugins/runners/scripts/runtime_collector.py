"""Run the collector in the installed Hermes interpreter and dependency layer.

Invoked with -I by install_service --hermes-launcher. No service writes here.
--check-core activates the installed dependency generation and checks imports,
without reading runner config, vending credentials or starting collection. PM may
recover its publication journal and leases the selected generation; no dependency
install, source update, gateway startup or full CLI bootstrap is requested.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--core-root', required=True)
    parser.add_argument('--check-core', action='store_true')
    args, remaining = parser.parse_known_args(argv)
    root = Path(args.core_root)
    if not root.is_absolute() or not (root / 'pm/environments.py').is_file():
        print('repair_core_unavailable', file=sys.stderr)
        return 1
    root = root.resolve()
    # Pin the public plugin namespace before core's regular plugins package can
    # shadow it. This package's __init__ imports stdlib only and registers nothing.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    import plugins.runners
    for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'):
        os.environ.pop(key, None)
    sys.path.insert(0, str(root))
    try:
        # Dependency activation only, as used by core's external worker boot.
        # Full hermes_bootstrap can complete source updates/relaunch the process.
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            from pm import environments
            if Path(environments.__file__).resolve() != root / 'pm/environments.py':
                raise RuntimeError('conflicting_activation')
            environments.activate_dependencies(root)
            from plugins.runners.scripts import run_collector
            run_collector.configure_core(root)
    except ImportError as exc:
        name = exc.name or 'unknown'
        if len(name) > 160 or not all(part.isidentifier() for part in name.split('.')):
            name = 'unknown'
        kind = 'ModuleNotFoundError' if isinstance(exc, ModuleNotFoundError) else 'ImportError'
        print(f'repair_core_unavailable:{kind}:{name}', file=sys.stderr)
        return 1
    except (Exception, SystemExit) as exc:
        # configure_core already sanitizes its dependency diagnostic.
        reason = str(exc) if type(exc).__name__ == 'CoreUnavailable' else 'repair_core_unavailable'
        print(reason, file=sys.stderr)
        return 1
    if args.check_core:
        print('production collector core imports OK')
        return 0
    return run_collector.main(remaining + ['--core-root', str(root)])


if __name__ == '__main__':
    raise SystemExit(main())
