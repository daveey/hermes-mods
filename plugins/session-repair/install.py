"""Install the session-repair cron job on this host (the hub). Idempotent; prints the plan unless --apply.

    python3 install.py                 # show what would change
    python3 install.py --apply         # write the wrapper script + config, create the cron job if missing
    python3 install.py --apply --mode live   # also switch the tick to live (default: shadow)
    python3 install.py --apply --failure-deliver telegram   # where a failed tick shows (default: bot-chat)

What it does:
1. `$HERMES_HOME/scripts/session_repair_tick.py`: a 3-line wrapper (Hermes cron runs only files under
   `$HERMES_HOME/scripts/`) that runs this plugin's run.py through the `~/.hermes/plugins/session-repair` link, so a
   hermes-sync update takes effect on the next tick.
2. `$HERMES_HOME/session-repair/config.json` `{"mode": "shadow"}` unless it exists (or `--mode` is given).
3. `hermes cron create "every 15m" --name session-repair --no-agent --script session_repair_tick.py --deliver local
   --failure-deliver bot-chat --catch-up off`, unless a job named `session-repair` exists (read from
   `$HERMES_HOME/cron/jobs.json`). An existing job whose `failure_deliver` differs is reconciled with
   `hermes cron edit <id> --failure-deliver <target>`, after the same target check. Successes stay local (silent); a failed tick (exit 1: the repair turn failed) is
   delivered to the profile's Bot Chat, a visible chat, instead of only `hermes cron list`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

JOB_NAME = 'session-repair'
SCRIPT_NAME = 'session_repair_tick.py'
SCHEDULE = 'every 15m'
FAILURE_DELIVER = 'bot-chat'
WRAPPER = '''#!/usr/bin/env python3
# session-repair tick (hermes-mods plugins/session-repair, installed by its install.py): silent unless something is new
import os, runpy
runpy.run_path(os.path.join(os.environ.get('HERMES_HOME') or os.path.expanduser('~/.hermes'), 'plugins', 'session-repair', 'run.py'), run_name='__main__')
'''


def home() -> Path:
    return Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')


def hermes() -> list[str]:
    found = shutil.which('hermes')
    return [found] if found else [sys.executable, '-m', 'hermes_cli.main']


def find_job(h: Path) -> dict | None:
    try:
        data = json.loads((h / 'cron' / 'jobs.json').read_text())
    except (OSError, ValueError):
        return None
    jobs = data.get('jobs', data) if isinstance(data, dict) else data
    jobs = jobs.values() if isinstance(jobs, dict) else jobs
    return next((j for j in jobs or [] if isinstance(j, dict) and j.get('name') == JOB_NAME), None)


def job_exists(h: Path) -> bool:
    return find_job(h) is not None


def edit_argv(job_id: str, failure_deliver: str) -> list[str]:
    return hermes() + ['cron', 'edit', job_id, '--failure-deliver', failure_deliver]


def cron_argv(failure_deliver: str = FAILURE_DELIVER) -> list[str]:
    if failure_deliver == 'local':
        raise ValueError('failure-deliver local would hide failed ticks; name a visible target')
    return hermes() + ['cron', 'create', SCHEDULE, '--name', JOB_NAME, '--no-agent', '--script', SCRIPT_NAME,
                       '--deliver', 'local', '--failure-deliver', failure_deliver, '--catch-up', 'off']


def failure_target_problem(h: Path, target: str) -> str | None:
    """Why `target` would not show a failed tick, else None. For `bot-chat[:profile]` (core's own helpers): the
    profile must have a canonical Bot Chat authority (`tools.bot_live_delivery.find_canonical_live_owner`) and
    warning notifications on for its platform (`gateway.warning_notifications.warning_notifications_enabled`, as
    cron's failure delivery checks): otherwise core books the failure notice `suppressed`. Other targets are not
    checked here."""
    if not target.startswith('bot-chat'):
        return None
    profile = target.partition(':')[2]
    try:
        from cron.scheduler_delivery import BOT_CHAT_POLICY_PLATFORM
        from gateway.warning_notifications import warning_notifications_enabled
        from hermes_cli.config_effective import load_user_config_effective
        from tools.bot_live_delivery import find_canonical_live_owner
        if profile:
            from hermes_cli.profiles import get_profile_dir
            home_dir = Path(get_profile_dir(profile))
        else:
            home_dir = h
    except Exception as e:  # noqa: BLE001 -- core unreadable: cannot vouch for the target
        return f'cannot check {target} with core ({type(e).__name__}: {e})'
    try:
        if find_canonical_live_owner(home_dir) is None:
            return f'{target}: the profile has no session store, so no canonical Bot Chat'
    except ValueError as e:
        return f'{target}: the profile authority is not ready ({e}); failures would only queue'
    except Exception as e:  # noqa: BLE001
        return f'{target}: canonical Bot Chat lookup failed ({type(e).__name__}: {e})'
    try:
        enabled = warning_notifications_enabled(BOT_CHAT_POLICY_PLATFORM,
                                                load_user_config_effective(home_dir / 'config.yaml'))
    except Exception as e:  # noqa: BLE001
        return f'{target}: warning-notification setting unreadable ({type(e).__name__}: {e})'
    if not enabled:
        return (f'{target}: warning notifications are suppressed for {BOT_CHAT_POLICY_PLATFORM} on that profile, so '
                'cron books a failure notice as suppressed')
    return None


def plan(h: Path, mode: str | None, failure_deliver: str = FAILURE_DELIVER) -> list[tuple[str, str]]:
    steps = []
    script = h / 'scripts' / SCRIPT_NAME
    if not script.exists() or script.read_text() != WRAPPER:
        steps.append(('write', str(script)))
    cfg = h / 'session-repair' / 'config.json'
    if mode or not cfg.exists():
        steps.append(('config', mode or 'shadow'))
    if not (h / 'plugins' / 'session-repair' / 'run.py').exists():
        steps.append(('warn', f'{h}/plugins/session-repair is not linked yet: run hermes-sync first'))
    job = find_job(h)
    if job is None or job.get('failure_deliver') != failure_deliver:
        problem = failure_target_problem(h, failure_deliver)
        if problem:
            steps.append(('refuse', f'{problem}: pass --failure-deliver <a visible target> (telegram, slack:<chat>, ...)'))
        elif job is None:
            steps.append(('cron', ' '.join(cron_argv(failure_deliver))))
        else:  # an existing job whose failures go elsewhere (or nowhere): bring it to the checked target
            steps.append(('reconcile', ' '.join(edit_argv(str(job.get('id')), failure_deliver))))
    return steps


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--apply', action='store_true')
    p.add_argument('--mode', choices=('off', 'shadow', 'live'))
    p.add_argument('--failure-deliver', default=FAILURE_DELIVER,
                   help='cron --failure-deliver target for failed ticks (default bot-chat; never local)')
    args = p.parse_args(argv)
    if args.failure_deliver == 'local':
        p.error('--failure-deliver local would hide failed ticks')
    h = home()
    steps = plan(h, args.mode, args.failure_deliver)
    if not steps:
        print('session-repair: already installed')
        return 0
    refused = [what for kind, what in steps if kind == 'refuse']
    for kind, what in steps:
        print(f'{"" if kind == "refuse" or (args.apply and not refused) else "would "}{kind}: {what}')
    if refused:
        return 2  # nothing applied: a failure target that cannot show failures is not installed
    for kind, what in steps:
        if not args.apply:
            continue
        if kind == 'write':
            path = Path(what)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(WRAPPER)
            path.chmod(0o755)
        elif kind == 'config':
            cfg = h / 'session-repair' / 'config.json'
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(json.dumps({'mode': what}) + '\n')
        elif kind in ('cron', 'reconcile'):
            argv = (cron_argv(args.failure_deliver) if kind == 'cron'
                    else edit_argv(str(find_job(h).get('id')), args.failure_deliver))
            r = subprocess.run(argv, capture_output=True, text=True)
            print(r.stdout.strip() or r.stderr.strip())
            if r.returncode != 0:
                return r.returncode
    return 0


if __name__ == '__main__':
    sys.exit(main())
