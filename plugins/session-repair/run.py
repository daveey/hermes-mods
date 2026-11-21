"""Session-repair tick: scan, dedupe, cap, then log (shadow) or hand the new findings to the repair chat (live).

Run every 15 min by a Hermes cron no-agent job (install.py). Silent (no stdout) when nothing is new, so the job
delivers nothing; one summary line otherwise.

Modes (`SESSION_REPAIR` env, else `$HERMES_HOME/session-repair/config.json` `{"mode": ...}`, default `shadow`):
- off: do nothing.
- shadow: scan and log what would be fixed / filed to `actions-shadow.jsonl`; no agent turn, no board write.
- live: start one agent turn in the chat titled "Session repair" (`hermes chat -c "Session repair"
  --create-if-missing`, the same chat every time) with prompt.md and the new findings; that turn applies the safe
  fixes and files the fix cards. This script itself never writes a board.

Dedupe: a fingerprint (rule + card, or rule + chat) handed over is not handed over again for 24 h (per mode).
Caps: findings that need a card (fix kind `card` or `human`) are handed over at most 5 per run and 20 per 24 h;
the rest wait (not marked seen) for a later run. Safe-fix findings are capped at 20 per run.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

HERE = Path(__file__).resolve().parent
MODES = ('off', 'shadow', 'live')
DEFAULT_MODE = 'shadow'
DEDUPE_S = 24 * 3600
CARDS_PER_RUN = 5
CARDS_PER_DAY = 20
FIXES_PER_RUN = 20
CHAT_TITLE = 'Session repair'
TURN_TIMEOUT_S = 45 * 60
SKILL = 'broken-sessions'


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001 -- outside core: the env var, else ~/.hermes
        return Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')


def state_dir(home: Optional[Path] = None) -> Path:
    d = (home or hermes_home()) / 'session-repair'
    d.mkdir(parents=True, exist_ok=True)
    return d


def mode(home: Optional[Path] = None) -> str:
    m = os.environ.get('SESSION_REPAIR')
    if not m:
        try:
            m = json.loads(((home or hermes_home()) / 'session-repair' / 'config.json').read_text()).get('mode')
        except (OSError, ValueError, AttributeError):
            m = None
    m = (m or DEFAULT_MODE).strip().lower()
    return m if m in MODES else DEFAULT_MODE


def load_scan():
    key = '_session_repair_scan'
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / 'scan.py')
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


# ── state files ────────────────────────────────────────────────────────────────────────────────────────────────────
def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def _write_json(p: Path, value) -> None:
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f'.{p.name}.')
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f, indent=1, default=str)
    os.replace(tmp, p)


def _append(p: Path, rows: list[dict]) -> None:
    if rows:
        with p.open('a') as f:
            for r in rows:
                f.write(json.dumps(r, default=str) + '\n')


def _ledger(p: Path, since: float) -> list[dict]:
    out = []
    try:
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and (r.get('at') or 0) >= since:
                out.append(r)
    except OSError:
        pass
    return out


class Dedupe:
    """fingerprint -> when it was last handed over (per mode), kept 24 h."""

    def __init__(self, path: Path, now: float):
        self.path, self.now = path, now
        raw = _read_json(path, {})
        self.seen = {k: v for k, v in (raw.items() if isinstance(raw, dict) else [])
                     if isinstance(v, (int, float)) and now - v < DEDUPE_S}

    def new(self, fingerprint: str) -> bool:
        return fingerprint not in self.seen

    def mark(self, fingerprints) -> None:
        for fp in fingerprints:
            self.seen[fp] = self.now
        _write_json(self.path, self.seen)


def needs_card(f: dict) -> bool:
    return (f.get('fix') or {}).get('kind') in ('card', 'human')


def select(new: list[dict], filed_24h: int) -> tuple[list[dict], list[dict]]:
    """(handed over now, deferred) under the caps: card-needing findings at most CARDS_PER_RUN per run and
    CARDS_PER_DAY per 24 h, safe fixes at most FIXES_PER_RUN per run."""
    room = max(0, min(CARDS_PER_RUN, CARDS_PER_DAY - filed_24h))
    take, later, fixes = [], [], 0
    for f in new:
        if needs_card(f):
            if room > 0:
                take.append(f)
                room -= 1
            else:
                later.append(f)
        elif fixes < FIXES_PER_RUN:
            take.append(f)
            fixes += 1
        else:
            later.append(f)
    return take, later


# ── the repair turn ───────────────────────────────────────────────────────────────────────────────────────────────
def hermes_argv() -> list[str]:
    env = os.environ.get('SESSION_REPAIR_HERMES')
    if env:
        return env.split()
    found = shutil.which('hermes')
    return [found] if found else [sys.executable, '-m', 'hermes_cli.main']


def build_prompt(take: list[dict], *, filed_24h: int, deferred: int) -> str:
    template = (HERE / 'prompt.md').read_text()
    room_day = max(0, CARDS_PER_DAY - filed_24h)
    body = json.dumps(take, indent=1, default=str)
    return (template.replace('{{SKILL}}', SKILL).replace('{{CARDS_PER_RUN}}', str(CARDS_PER_RUN))
            .replace('{{CARDS_LEFT_TODAY}}', str(room_day)).replace('{{DEFERRED}}', str(deferred))
            .replace('{{COUNT}}', str(len(take))).replace('{{FINDINGS}}', body))


def start_turn(prompt: str, sd: Path, runner: Callable[..., Any] = subprocess.run) -> tuple[bool, str]:
    """One turn in the "Session repair" chat (created on first use). (ok, detail)."""
    path = sd / 'last-prompt.md'
    path.write_text(prompt)
    argv = hermes_argv() + ['chat', '-c', CHAT_TITLE, '--create-if-missing', '--oneshot', '-Q', '--source', 'cli',
                            '--query-file', str(path)]
    try:
        r = runner(argv, capture_output=True, text=True, timeout=TURN_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f'{type(e).__name__}: {e}'
    tail = ((r.stdout or '')[-1500:] + ('\n' + (r.stderr or '')[-800:] if r.stderr else '')).strip()
    (sd / 'last-turn.txt').write_text(tail)
    return r.returncode == 0, f'rc={r.returncode}'


def pin_chat(sd: Path, runner: Callable[..., Any] = subprocess.run, home: Optional[Path] = None) -> Optional[str]:
    """Pin the repair chat once (CLI-created chats are hidden from the Desktop sidebar unless pinned)."""
    import sqlite3
    db = (home or hermes_home()) / 'state.db'
    if not db.exists():
        return None
    try:
        # mode=rw + query_only, never a read-only URI (tests/test_sqlite_ro_guard.py): reads only, creates nothing.
        c = sqlite3.connect(f'file:{db}?mode=rw', uri=True, timeout=5)
        try:
            c.execute('PRAGMA query_only=ON')
            row = c.execute('SELECT id, pinned FROM sessions WHERE title = ? AND archived = 0 ORDER BY started_at DESC '
                            'LIMIT 1', (CHAT_TITLE,)).fetchone()
        finally:
            c.close()
    except sqlite3.Error:  # pinning is a convenience; the turn already happened
        return None
    if not row:
        return None
    if not row[1]:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            runner(hermes_argv() + ['sessions', 'pin', str(row[0])], capture_output=True, text=True, timeout=60)
    _write_json(sd / 'chat.json', {'session': row[0]})
    return str(row[0])


# ── one tick ──────────────────────────────────────────────────────────────────────────────────────────────────────
@contextlib.contextmanager
def _lock(sd: Path):
    import fcntl
    f = (sd / 'run.lock').open('w')
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def tick(*, home: Optional[Path] = None, scanner: Optional[Callable[[], dict]] = None,
         runner: Callable[..., Any] = subprocess.run, now: Optional[float] = None,
         run_mode: Optional[str] = None) -> dict:
    """One pass. Returns {mode, new, handed, deferred, turn, line} (line = what stdout prints, '' = silent)."""
    m = run_mode or mode(home)
    out: dict[str, Any] = {'mode': m, 'new': 0, 'handed': [], 'deferred': 0, 'turn': None, 'line': ''}
    if m == 'off':
        return out
    now = time.time() if now is None else now
    sd = state_dir(home)
    with _lock(sd) as held:
        if not held:
            out['line'] = ''
            out['skipped'] = 'locked'
            return out
        result = (scanner or (lambda: load_scan().scan()))()
        findings = result.get('findings') or []
        dedupe = Dedupe(sd / f'seen-{m}.json', now)
        new = [f for f in findings if dedupe.new(f['fingerprint'])]
        filed_24h = sum(1 for r in _ledger(sd / f'ledger-{m}.jsonl', now - DEDUPE_S) if r.get('card_needed'))
        take, later = select(new, filed_24h)
        _write_json(sd / 'last.json', {**result, 'mode': m, 'new': [f['fingerprint'] for f in new],
                                       'handed': [f['fingerprint'] for f in take], 'deferred': len(later),
                                       'tick_at': now})
        out.update(new=len(new), deferred=len(later))
        if not take:
            return out
        if m == 'live':
            ok, detail = start_turn(build_prompt(take, filed_24h=filed_24h, deferred=len(later)), sd, runner)
            out['turn'] = detail
            if not ok:  # nothing marked seen: the next tick hands the same findings over again
                out['line'] = f'session-repair: repair turn failed ({detail}); {len(take)} finding(s) kept for retry'
                out['failed'] = True
                return out
            pin_chat(sd, runner, home)
        rows = [{'at': now, 'mode': m, 'fingerprint': f['fingerprint'], 'rule': f['id'], 'variant': f.get('variant'),
                 'card': f.get('card'), 'board': f.get('board'), 'chat': f.get('chat'),
                 'would' if m == 'shadow' else 'handed': 'file' if needs_card(f) else 'fix',
                 'card_needed': needs_card(f), 'commands': (f.get('fix') or {}).get('commands'),
                 'summary': f.get('summary')} for f in take]
        _append(sd / f'ledger-{m}.jsonl', rows)
        if m == 'shadow':
            _append(sd / 'actions-shadow.jsonl', rows)
        dedupe.mark(f['fingerprint'] for f in take)
        out['handed'] = [f['fingerprint'] for f in take]
        fixes = sum(1 for f in take if not needs_card(f))
        verb = 'would fix' if m == 'shadow' else 'handed to the repair chat:'
        out['line'] = (f'session-repair ({m}): {len(new)} new finding(s); {verb} {fixes} fix(es), '
                       f'{len(take) - fixes} card(s); {len(later)} deferred by caps')
        return out


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description='One session-repair tick (cron no-agent job).')
    p.add_argument('--mode', choices=MODES, help='override SESSION_REPAIR / config.json for this run')
    p.add_argument('--scan-only', action='store_true', help='print the scan JSON; touch no state')
    args = p.parse_args(argv)
    if args.scan_only:
        print(json.dumps(load_scan().scan(), indent=2, default=str))
        return 0
    out = tick(run_mode=args.mode)
    if out['line']:
        print(out['line'])
    return 1 if out.get('failed') else 0


if __name__ == '__main__':
    sys.exit(main())
