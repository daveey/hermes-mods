"""Read-only host/profile landing coverage audit; never changes override semantics."""
import argparse
import json
import runpy
from pathlib import Path

landing_repos = runpy.run_path(str(Path(__file__).with_name('guard.py')))['landing_repos']


def audit(home, required):
    home = Path(home)
    required = {repo.lower() for repo in required}
    rows = []
    homes = [home]
    try:
        homes.extend(sorted((home / 'profiles').iterdir()))
    except FileNotFoundError:
        pass
    except OSError as exc:
        rows.append({'home': str(home / 'profiles'), 'error': str(exc), 'ok': False})
    for candidate in homes:
        try:
            # Inspect every directory, not just profiles with config.yaml: a
            # dormant core-only override is still a future coverage regression.
            if not candidate.is_dir():
                raise ValueError('Home/profile entry is not a readable directory')
            repos = landing_repos(candidate)
            missing = sorted(required - repos)
            rows.append({'home': str(candidate), 'repos': sorted(repos),
                         'missing': missing, 'ok': not missing})
        except (OSError, ValueError) as exc:
            rows.append({'home': str(candidate), 'error': str(exc), 'ok': False})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True, help='host Hermes home (not a profile)')
    parser.add_argument('--require', action='append', required=True, metavar='OWNER/REPO',
                        help='required fleet landing repository; repeat for each repository')
    args = parser.parse_args()
    rows = audit(args.home, args.require)
    print(json.dumps(rows, indent=2))
    return 0 if all(row['ok'] for row in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
