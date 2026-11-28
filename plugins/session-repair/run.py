"""Session-repair tick: scan, dedupe, cap, then log (shadow) or hand the new findings to the repair chat (live).

Run every 15 min by a Hermes cron no-agent job (install.py). Silent (no stdout) when nothing is new, so the job
delivers nothing; one summary line otherwise; exit 1 when the repair turn failed (the job's failure target shows it).

Modes (`SESSION_REPAIR` env, else `$HERMES_HOME/session-repair/config.json` `{"mode": ...}`, default `shadow`):
- off: do nothing.
- shadow: scan and log what would be fixed / filed to `actions-shadow.jsonl`; no agent turn, no board write.
- live: start one agent turn in the chat titled "Session repair" (`hermes chat -c "Session repair"
  --create-if-missing`, the same chat every time) with prompt.md and the new findings; that turn applies the safe
  fixes and files the fix cards, and ends with a `session-repair-result` block naming each finding's outcome. This
  script itself never writes a board.

Seen (24 h dedupe per fingerprint) only once handled: shadow marks what it logged; live marks what the turn's result
block reports handled (`fixed`, `card`, `queued`, `commented`, `moved_on`, `false_positive`, or `deferred` with a
reason). `over_budget` comes back next tick with no attempt counted, only when the turn reported as many cards as
its budget; an unknown outcome, a `deferred` with no reason or
a finding the block leaves out is a failed attempt (backoff, `gave_up` after 5). Caps: at most 5 fix cards per run and 20 per 24 h (cards
the turn reports, including cards for safe fixes that did not take); safe fixes at most 20 per run. A failed turn
(non-zero exit, no result block) backs off per fingerprint (15 min doubling, at most 6 h) and globally; after 5
failed attempts a fingerprint is given up for 24 h and logged.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import re
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
CHAT_TITLE = 'Session repair (routed)'
TURN_TIMEOUT_S = 45 * 60
SKILL = 'broken-sessions'
BACKOFF_BASE_S = 15 * 60
BACKOFF_MAX_S = 6 * 3600
MAX_ATTEMPTS = 5
LOG_MAX_LINES = 2000          # ledger-*.jsonl / actions-shadow.jsonl keep their newest lines only
RESULT_FENCE = 'session-repair-result'
HANDLED = ('fixed', 'card', 'queued', 'commented', 'moved_on', 'false_positive', 'deferred')  # seen for 24 h
RETRY_NEXT_TICK = ('over_budget',)  # the turn ran out of card budget: back next tick, no attempt counted
OUTCOMES = HANDLED + RETRY_NEXT_TICK  # anything else (or a finding left out) is a failed attempt
CARD_OUTCOMES = ('card', 'queued')


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


def live_rules(home: Optional[Path] = None) -> Optional[frozenset]:
    """config.json `live_rules`: the `<rule id>:<variant>` findings live mode may act on. None (key absent) = every
    finding, as before. Any other value that is not a list of strings fails closed to the empty set (nothing live):
    a partial go-live never widens because its allowlist was unreadable."""
    try:
        data = json.loads(((home or hermes_home()) / 'session-repair' / 'config.json').read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or 'live_rules' not in data:
        return None
    value = data['live_rules']
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        return frozenset()
    return frozenset(x.strip() for x in value)


def rule_key(f: dict) -> str:
    return f'{f.get("id")}:{f.get("variant")}'


# Rules that stay shadow (log only) whatever `mode` / `nudge_mode` say, until config.json `shadow_rules` lists them
# no more. A new rule soaks here first: BS-no-progress shadows 24 h before it may nudge or file (`"shadow_rules": []`).
DEFAULT_SHADOW_RULES = frozenset({'BS-no-progress'})


def shadow_rules(home: Optional[Path] = None) -> frozenset:
    """config.json `shadow_rules` (rule ids), else DEFAULT_SHADOW_RULES. An unreadable value keeps the default: a
    soak never ends because its setting was malformed."""
    try:
        data = json.loads(((home or hermes_home()) / 'session-repair' / 'config.json').read_text())
    except (OSError, ValueError):
        return DEFAULT_SHADOW_RULES
    value = data.get('shadow_rules') if isinstance(data, dict) else None
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return frozenset(x.strip() for x in value)
    return DEFAULT_SHADOW_RULES


def _rule_shadow(sd: Path, now: float, findings: list[dict]) -> str:
    """Log a soaking rule's findings to actions-shadow.jsonl (every tick: the rule's own windows space them; no Seen
    mark, so the soak sees each first and second miss). Returns the summary line."""
    if not findings:
        return ''
    rows = [_row(now, 'shadow', f, would='file' if needs_card(f) else (f.get('fix') or {}).get('kind'),
                 outcome='rule_shadow') for f in findings]
    _append(sd / 'actions-shadow.jsonl', rows)
    by: dict[str, int] = {}
    for f in findings:
        by[rule_key(f)] = by.get(rule_key(f), 0) + 1
    return 'rule shadow: ' + ', '.join(f'{k} {v}' for k, v in sorted(by.items()))


def load_progress():
    return load_scan().load_progress()


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
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def _append(p: Path, rows: list[dict], max_lines: int = LOG_MAX_LINES) -> None:
    """Append rows; keep only the newest `max_lines` lines (bounded logs)."""
    if not rows:
        return
    with p.open('a') as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + '\n')
    try:
        lines = p.read_text().splitlines()
    except OSError:
        return
    if len(lines) > max_lines:
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f'.{p.name}.')
        with os.fdopen(fd, 'w') as f:
            f.write('\n'.join(lines[-max_lines:]) + '\n')
        os.replace(tmp, p)


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
    """fingerprint -> when it was last handled (per mode), kept 24 h."""

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


class Attempts:
    """Failed repair turns: per fingerprint `{n, next_at}` and a global `{failures, next_at}` (exponential backoff)."""

    def __init__(self, path: Path, now: float):
        self.path, self.now = path, now
        raw = _read_json(path, {})
        self.fps: dict = raw.get('fps', {}) if isinstance(raw, dict) else {}
        self.turn: dict = raw.get('turn', {}) if isinstance(raw, dict) else {}

    @staticmethod
    def delay(n: int) -> float:
        return min(BACKOFF_BASE_S * 2 ** max(0, n - 1), BACKOFF_MAX_S)

    def waiting(self, fp: str) -> bool:
        return (self.fps.get(fp) or {}).get('next_at', 0) > self.now

    def turn_waiting(self) -> bool:
        return self.turn.get('next_at', 0) > self.now

    def failed(self, fps: list[str], *, turn: bool = True) -> list[str]:
        """Count one failed attempt for each (and, with `turn`, one failed turn); returns the ones that reached
        MAX_ATTEMPTS (given up)."""
        gave_up = []
        for fp in fps:
            n = int((self.fps.get(fp) or {}).get('n', 0)) + 1
            if n >= MAX_ATTEMPTS:
                self.fps.pop(fp, None)
                gave_up.append(fp)
            else:
                self.fps[fp] = {'n': n, 'next_at': self.now + self.delay(n)}
        if turn:
            failures = int(self.turn.get('failures', 0)) + 1
            self.turn = {'failures': failures, 'next_at': self.now + self.delay(failures)}
        self.save()
        return gave_up

    def succeeded(self, handled: list[str]) -> None:
        for fp in handled:
            self.fps.pop(fp, None)
        self.turn = {}
        self.save()

    def save(self) -> None:
        _write_json(self.path, {'fps': self.fps, 'turn': self.turn})


def needs_card(f: dict) -> bool:
    return (f.get('fix') or {}).get('kind') == 'card'


def select(new: list[dict], card_room: int) -> tuple[list[dict], list[dict]]:
    """(handed over now, deferred): card findings at most `card_room` (min of 5 per run and what is left of 20 per
    24 h), safe fixes at most FIXES_PER_RUN per run."""
    room = max(0, card_room)
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


def idempotency_key(fingerprint: str, now: float) -> str:
    """`session-repair:<fingerprint>:<UTC day>`: a recurrence on a later day is a new card, never absorbed into a
    finished one (the prompt also has the turn check for an open card first)."""
    return f'session-repair:{fingerprint}:{time.strftime("%Y%m%d", time.gmtime(now))}'


# ── the repair turn ───────────────────────────────────────────────────────────────────────────────────────────────
def hermes_argv() -> list[str]:
    env = os.environ.get('SESSION_REPAIR_HERMES')
    if env:
        return env.split()
    found = shutil.which('hermes')
    return [found] if found else [sys.executable, '-m', 'hermes_cli.main']


def prepare_consumer(sd: Path) -> str:
    spec = importlib.util.spec_from_file_location('_session_repair_consumer', HERE / 'consumer.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sid = mod.prepare()
    _write_json(sd / 'chat.json', {'session': sid})
    return sid


def turn_argv(prompt_path: Path, session: str) -> list[str]:
    # Resume only the policy-verified class-routed session, not the legacy
    # titled CLI path (which creates with profile defaults, discarding flags).
    return hermes_argv() + ['chat', '--resume', session, '--oneshot', '-Q', '--source', 'cli',
                            '--model', 'class:standard', '--provider', 'auto', '--query-file', str(prompt_path)]


def build_prompt(take: list[dict], *, card_budget: int, deferred: int) -> str:
    template = (HERE / 'prompt.md').read_text()
    body = json.dumps(take, indent=1, default=str)
    return (template.replace('{{SKILL}}', SKILL).replace('{{CARD_BUDGET}}', str(card_budget))
            .replace('{{DEFERRED}}', str(deferred)).replace('{{COUNT}}', str(len(take)))
            .replace('{{RESULT_FENCE}}', RESULT_FENCE).replace('{{FINDINGS}}', body))


def parse_result(text: str) -> Optional[dict[str, dict]]:
    """{fingerprint: {outcome, card}} from the turn's last ```session-repair-result``` block, else None."""
    blocks = re.findall(r'```' + re.escape(RESULT_FENCE) + r'\s*\n(.*?)```', text or '', re.S)
    if not blocks:
        return None
    try:
        data = json.loads(blocks[-1])
    except ValueError:
        return None
    rows = data.get('results') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    out = {}
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get('fingerprint'), str):
            out[r['fingerprint']] = {'outcome': str(r.get('outcome') or ''), 'card': r.get('card'),
                                     'reason': str(r.get('reason') or ''), 'commented_on': r.get('commented_on')}
    return out


def start_turn(prompt: str, sd: Path, runner: Callable[..., Any] = subprocess.run) -> tuple[bool, str, str]:
    """One turn in the "Session repair" chat (created on first use). (ok, detail, stdout)."""
    path = sd / 'last-prompt.md'
    path.write_text(prompt)
    try:
        session = prepare_consumer(sd)
    except (ImportError, OSError, ValueError) as e:
        return False, f'consumer preparation: {type(e).__name__}: {e}', ''
    try:
        r = runner(turn_argv(path, session), capture_output=True, text=True, timeout=TURN_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f'{type(e).__name__}: {e}', ''
    tail = ((r.stdout or '')[-4000:] + ('\n' + (r.stderr or '')[-800:] if r.stderr else '')).strip()
    (sd / 'last-turn.txt').write_text(tail)
    return r.returncode == 0, f'rc={r.returncode}', r.stdout or ''


def pin_chat(sd: Path, runner: Callable[..., Any] = subprocess.run, home: Optional[Path] = None) -> Optional[str]:
    """Pin the repair chat once (CLI-created chats are hidden from the Desktop sidebar unless pinned) and record its
    id in chat.json (the scanner never scans or re-delivers into it)."""
    import sqlite3
    db = (home or hermes_home()) / 'state.db'
    if not db.exists():
        return None
    try:
        # mode=rw + query_only, never a read-only URI (tests/test_sqlite_ro_guard.py): reads only, creates nothing.
        c = sqlite3.connect(f'file:{db}?mode=rw', uri=True, timeout=5)
        try:
            c.execute('PRAGMA query_only=ON')
            sid = _read_json(sd / 'chat.json', {}).get('session')
            if not isinstance(sid, str) or not sid:
                return None
            row = c.execute('SELECT id, pinned FROM sessions WHERE id = ? AND archived = 0', (sid,)).fetchone()
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


def _row(now: float, m: str, f: dict, **extra) -> dict:
    return {'at': now, 'mode': m, 'fingerprint': f['fingerprint'], 'rule': f['id'], 'variant': f.get('variant'),
            'card': f.get('card'), 'board': f.get('board'), 'chat': f.get('chat'), 'area': f.get('area'),
            'commands': (f.get('fix') or {}).get('commands'), 'summary': f.get('summary'),
            'evidence': f.get('evidence') or {}, **extra}


def _ordinary_tick(*, home: Optional[Path] = None, scanner: Optional[Callable[[], dict]] = None,
         runner: Callable[..., Any] = subprocess.run, now: Optional[float] = None,
         run_mode: Optional[str] = None) -> dict:
    """One pass. Returns {mode, new, handed, deferred, turn, line} (line = what stdout prints, '' = silent)."""
    m = run_mode or mode(home)
    out: dict[str, Any] = {'mode': m, 'new': 0, 'handed': [], 'deferred': 0, 'turn': None, 'line': ''}
    if m == 'off':
        return out
    now = time.time() if now is None else now
    sd = state_dir(home)
    with contextlib.nullcontext(True) as held:  # _tick owns run.lock for both lanes
        if not held:
            out['skipped'] = 'locked'
            return out
        result = (scanner or (lambda: load_scan().scan()))()
        findings = result.get('findings') or []
        dedupe = Dedupe(sd / f'seen-{m}.json', now)
        attempts = Attempts(sd / f'attempts-{m}.json', now)
        new = [f for f in findings if dedupe.new(f['fingerprint'])]
        ready = [f for f in new if not attempts.waiting(f['fingerprint'])]
        ledger = sd / f'ledger-{m}.jsonl'
        filed_24h = sum(1 for r in _ledger(ledger, now - DEDUPE_S) if r.get('outcome') in CARD_OUTCOMES)
        card_budget = max(0, min(CARDS_PER_RUN, CARDS_PER_DAY - filed_24h))
        take, later = select(ready, card_budget)
        for f in take:
            f['idempotency_key'] = idempotency_key(f['fingerprint'], now)
        _write_json(sd / 'last.json', {**result, 'mode': m, 'new': [f['fingerprint'] for f in new],
                                       'handed': [f['fingerprint'] for f in take], 'deferred': len(later),
                                       'backing_off': len(new) - len(ready), 'tick_at': now})
        out.update(new=len(new), deferred=len(later) + len(new) - len(ready))
        if not take:
            return out
        if m == 'shadow':
            rows = [_row(now, m, f, would='file' if needs_card(f) else 'fix',
                         outcome='card' if needs_card(f) else 'fixed') for f in take]
            _append(ledger, rows)
            _append(sd / 'actions-shadow.jsonl', rows)
            dedupe.mark(f['fingerprint'] for f in take)
            out['handed'] = [f['fingerprint'] for f in take]
            fixes = sum(1 for f in take if not needs_card(f))
            out['line'] = (f'session-repair (shadow): {len(new)} new finding(s); would fix {fixes}, '
                           f'file {len(take) - fixes} card(s); {out["deferred"]} deferred')
            return out
        if attempts.turn_waiting():
            out['skipped'] = 'backoff'
            return out
        ok, detail, stdout = start_turn(build_prompt(take, card_budget=card_budget, deferred=len(later)), sd, runner)
        out['turn'] = detail
        results = parse_result(stdout) if ok else None
        fps = [f['fingerprint'] for f in take]
        if results is None:
            gave_up = attempts.failed(fps)
            if gave_up:
                dedupe.mark(gave_up)
                _append(ledger, [_row(now, m, f, outcome='gave_up') for f in take if f['fingerprint'] in gave_up])
            out['failed'] = True
            why = detail if not ok else 'no result block'
            out['line'] = (f'session-repair: repair turn failed ({why}); {len(fps) - len(gave_up)} finding(s) retry after '
                           f'backoff, {len(gave_up)} given up after {MAX_ATTEMPTS} attempts')
            return out
        pin_chat(sd, runner, home)

        created = sum(1 for fp in fps if (results.get(fp) or {}).get('outcome') in CARD_OUTCOMES)

        def accepted(fp: str) -> Optional[str]:
            r = results.get(fp)
            if r is None or r['outcome'] not in OUTCOMES or (r['outcome'] == 'deferred' and not r['reason'].strip()):
                return None  # left out, unknown, or a deferral with no reason: a failed attempt
            if r['outcome'] == 'over_budget' and created < card_budget:
                return None  # "over budget" while the budget was not used up: a failed attempt
            return r['outcome']
        handled = [fp for fp in fps if accepted(fp) in HANDLED]
        bad = [fp for fp in fps if accepted(fp) is None]
        by_fp = {f['fingerprint']: f for f in take}
        _append(ledger, [_row(now, m, by_fp[fp], outcome=results[fp]['outcome'],
                              filed=results[fp].get('card') if results[fp]['outcome'] in CARD_OUTCOMES else None,
                              commented_on=results[fp].get('commented_on'), reason=results[fp]['reason'] or None)
                         for fp in fps if fp in results])
        dedupe.mark(handled)
        attempts.succeeded(handled)
        if bad:  # the turn ran but did not account for these: retried after backoff, never silently dropped
            gave_up = attempts.failed(bad, turn=False)
            if gave_up:
                dedupe.mark(gave_up)
                _append(ledger, [_row(now, m, by_fp[fp], outcome='gave_up') for fp in gave_up])
        out['handed'] = handled
        cards = sum(1 for fp in handled if results[fp]['outcome'] in CARD_OUTCOMES)
        out['line'] = (f'session-repair (live): {len(new)} new finding(s); handled {len(handled)} ({cards} card(s)), '
                       f'{len(fps) - len(handled)} left for a later tick; {out["deferred"]} deferred')
        return out


def load_nudge():
    key = '_session_repair_nudge'
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / 'nudge.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[key] = mod
    return sys.modules[key]


def _tick(*, home=None, scanner=None, runner=subprocess.run, now=None, run_mode=None):
    m = run_mode or mode(home)
    out = {'mode':m,'new':0,'handed':[],'deferred':0,'turn':None,'line':''}
    if m == 'off':
        return out
    now = time.time() if now is None else now
    home = home or hermes_home()
    sd = state_dir(home)
    with _lock(sd) as held:
        if not held:
            return {**out,'skipped':'locked'}
        result = (scanner or (lambda: load_scan().scan()))()
        snapshots = result.pop('_progress_snapshots', None)
        if snapshots:
            try:
                load_progress().persist(snapshots, home, now)
            except Exception as e:  # noqa: BLE001 -- a lost snapshot only restarts that chat's baseline
                result.setdefault('errors', []).append({'where': 'progress persist', 'error': f'{type(e).__name__}: {e}'[:300]})
        soaking = shadow_rules(home)
        soak = [f for f in result.get('findings') or [] if f.get('id') in soaking]
        findings = [f for f in result.get('findings') or [] if f.get('id') not in soaking]
        soak_line = _rule_shadow(sd, now, soak)
        nudges = [f for f in findings if (f.get('fix') or {}).get('kind')=='nudge']
        nm = load_nudge().config_mode(home)
        config = _read_json(sd/'config.json', {})
        allowed = live_rules(home) if m == 'live' else None
        red_rule = 'BS-red-no-ask'
        red_key = red_rule + ':uncovered_red_cards'
        # Explicit, scoped rollout. Neither this override nor a malformed value can
        # promote other nudge rules; mode=off returned before scanning.
        override = config.get('nudge_live_rules', []) if isinstance(config, dict) else []
        red_live = (m == 'live' and isinstance(override, list) and red_rule in override
                    and (allowed is None or red_key in allowed or red_rule in allowed)
                    and red_rule not in soaking)
        split_red = red_live and nm != 'live'
        actions, escalations, nm = load_nudge().process(
            nudges,home=home,now=now,scan=load_scan(),runner=runner,argv=hermes_argv(),
            read_json=_read_json,write_json=_write_json,append=_append,
            hold=soaking | ({red_rule} if split_red else set()))
        red_escalations = []
        if split_red:
            red_actions, red_escalations, _ = load_nudge().process(
                [f for f in nudges if f.get('id') == red_rule], home=home, now=now,
                scan=load_scan(),runner=runner,argv=hermes_argv(),
                read_json=_read_json,write_json=_write_json,append=_append,run_mode='live',
                hold=frozenset(load_scan().RULES) - {red_rule})
            actions += red_actions
        ordinary = [f for f in findings if (f.get('fix') or {}).get('kind')!='nudge']
        held_back = []
        allowed = live_rules(home) if m == 'live' else None
        if allowed is not None:  # partial go-live: only allowlisted rule:variant findings act; the rest stay shadow
            held_back = [f for f in ordinary if rule_key(f) not in allowed]
            ordinary = [f for f in ordinary if rule_key(f) in allowed]
        out = _ordinary_tick(home=home,scanner=lambda:{**result,'findings':ordinary+(escalations if nm==m else [])},
                             now=now,run_mode=m,runner=runner)
        if held_back:
            shadow = _ordinary_tick(home=home,scanner=lambda:{**result,'findings':held_back},now=now,
                                    run_mode='shadow',runner=runner)
            out['line'] += ('; ' if out['line'] and shadow['line'] else '') + shadow['line']
            out['shadowed'] = shadow['handed']
        if escalations and nm != m:
            extra = _ordinary_tick(home=home,scanner=lambda:{**result,'findings':escalations},now=now,run_mode=nm,runner=runner)
            out['handed'] += extra['handed']
            out['failed'] = out.get('failed',False) or extra.get('failed',False)
            out['line'] += ('; ' if out['line'] else '') + extra['line']
        if red_escalations:
            extra = _ordinary_tick(home=home,scanner=lambda:{**result,'findings':red_escalations},
                                   now=now,run_mode='live',runner=runner)
            out['handed'] += extra['handed']
            out['failed'] = out.get('failed',False) or extra.get('failed',False)
            out['line'] += ('; ' if out['line'] else '') + extra['line']
            path = sd/'nudges-live.json'
            state = _read_json(path, {'fps':{}, 'sent':[]})
            for f in red_escalations:
                if f['fingerprint'] in extra['handed']:
                    state['fps'][f['fingerprint']]['escalated'] = now
            _write_json(path, state)
        out['nudge_live_rules'] = [red_rule] if red_live else []
        out['nudges'], out['nudge_mode'] = actions, nm
        if any(a.get('outcome') == 'delivery_unknown' for a in actions):
            out['failed'] = True
        if escalations:
            path = sd / f'nudges-{nm}.json'
            state = _read_json(path, {'fps':{}, 'sent':[]})
            for f in escalations:
                if f['fingerprint'] in out['handed']:
                    state['fps'][f['fingerprint']]['escalated'] = now
            _write_json(path, state)
        _write_json(sd/'last.json',{**_read_json(sd/'last.json', {}), **result,'mode':m,'nudge_mode':nm,'tick_at':now,
                                  'handed':out['handed'],'nudges':actions})
        if actions:
            out['line'] += ('; ' if out['line'] else '') + f'session-repair nudges ({nm}): {len(actions)} outcome(s)'
        prog = result.get('progress') or {}
        out['progress'] = prog.get('verdicts')
        out['rule_shadow'] = [f['fingerprint'] for f in soak]
        if soak_line:  # silent otherwise: verdict counts go to ticks.jsonl every tick
            counts = ', '.join(f'{k} {v}' for k, v in (prog.get('verdicts') or {}).items())
            out['line'] += ('; ' if out['line'] else '') + f'{soak_line} (BS-no-progress verdicts: {counts})'
        return out


TICK_LOG_MAX_BYTES = 4 * 1024 * 1024  # current + one rotated segment; months at 15-minute cadence


def _tick_summary(sd: Path, row: dict) -> None:
    """Append immutable tick records, rotating whole segments rather than rewriting history."""
    import fcntl
    data = (json.dumps(row, default=str) + '\n').encode()
    with (sd / 'ticks.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = sd / 'ticks.jsonl'
        if path.exists() and path.stat().st_size + len(data) > TICK_LOG_MAX_BYTES:
            os.replace(path, sd / 'ticks.jsonl.1')
        with path.open('ab') as log:
            log.write(data)
            log.flush()
            os.fsync(log.fileno())


def tick(*, home: Optional[Path] = None, scanner: Optional[Callable[[], dict]] = None,
         runner: Callable[..., Any] = subprocess.run, now: Optional[float] = None,
         run_mode: Optional[str] = None) -> dict:
    """Persist one summary even for silent, off, locked, scan-error and failed-turn ticks."""
    import hashlib
    started = time.monotonic()
    ts = time.time() if now is None else now
    m = run_mode or mode(home)
    result, out, errors = {}, {}, []

    def observe():
        nonlocal result
        value = (scanner or (lambda: load_scan().scan()))()
        if not isinstance(value, dict):
            raise TypeError('scanner must return a dict')
        result = value
        return result

    try:
        out = _tick(home=home, scanner=observe, runner=runner, now=ts, run_mode=m)
        return out
    except Exception as exc:
        errors.append(f'{type(exc).__name__}: {str(exc)[:500]}')
        raise
    finally:
        errors.extend(str(e)[:500] for e in result.get('errors', []))
        if out.get('failed'):
            errors.append(str(out.get('line') or 'repair turn failed')[:500])
        # Include a source fingerprint so a soak cannot silently span a code change.
        build = hashlib.sha256(b''.join((HERE / name).read_bytes() for name in ('scan.py', 'run.py', 'nudge.py', 'producer.py', 'target_reader.py', 'progress.py'))).hexdigest()
        _tick_summary(state_dir(home), {
            'ts': ts, 'mode': m, 'build': build, 'chats': result.get('chats', 0),
            'cards': result.get('cards', 0), 'findings': len(result.get('findings') or []),
            'errors': errors[:20], 'error_count': max(len(errors), result.get('error_count', 0)),
            'duration_s': round(time.monotonic() - started, 6),
            'scanned': bool(result), 'skipped': out.get('skipped'),
            'handed': out.get('handed', []), 'nudge_mode': out.get('nudge_mode'),
            'nudges': out.get('nudges', []),
            'progress': (result.get('progress') or {}).get('verdicts'), 'rule_shadow': out.get('rule_shadow', []),
            'red_no_ask': result.get('red_no_ask'), 'nudge_live_rules': out.get('nudge_live_rules', []),
        })


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
