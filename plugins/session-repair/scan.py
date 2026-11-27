"""Session-repair scanner: finds chats whose Kanban cards sit in a weird or stuck state. Read-only.

[daveey 2026-11-20] "a scheduled job that scans sessions for weird or stuck statuses — like tasks waiting for workers
if a worker is available, unexplained waits, etc." Each rule here is one pattern of the `broken-sessions` skill
(skills/devops/broken-sessions/SKILL.md); its id (`BS-...`) is the skill's section id. A finding names the rule, the
card, the chat, the evidence the rule read and the fix the skill prescribes.

What is scanned: every chat of this gateway with an open linked card (`tasks.session_id`, or an unreleased
`task_chat_links` row for this gateway), not only the chats a Desktop window watches. Each chat is walked with the
session-panel feed (`plugins/session-panel/dashboard/feed.py` `Feed.compute` over its `CoreReaders`), so every card
on a linked card's dependency chain is judged too; and, for BS-notice-undelivered, every chat in `state.db`.

Read-only by construction: every SQLite handle comes from the session-panel `CoreReaders.conn` (`mode=rw` +
`PRAGMA query_only=ON`), so a core helper that tried to write would raise, never write. Core helpers are used
for the views the dispatcher acts on (`kanban_continuation.park_state` / `missing_wake`, `kanban_queue_position.read`,
`kanban_human_request.needs_human`); each is wrapped, so one card's failure is a counted error, not a crash.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

log = logging.getLogger('session-repair')

TERMINAL = ('done', 'archived', 'cancelled')
SATISFIED = ('done', 'archived')  # core recompute_ready: a parent satisfies its child only done or archived

# Thresholds (seconds). The skill's pattern sections quote these; change both together.
READY_UNCLAIMED_S = 10 * 60        # BS-ready-worker-free, worker lanes (core kanban.sync.pickup_stall_seconds default)
# BS-ready-worker-free, local lane: core kanban_dispatch_skip.unrecorded() (30 min, free slot, no recorded skip)
WAKE_OVERDUE_S = 30 * 60           # BS-overdue-wake
AGENT_BLOCK_S = 2 * 3600           # BS-unexplained-wait (agent-owned block)
NO_REASON_S = 30 * 60              # BS-unexplained-wait (blocked with no kind and no reason)
PARENT_DONE_S = 10 * 60            # BS-parent-done
RUN_QUIET_S = 60 * 60              # BS-stuck-run
CLAIM_GRACE_S = 5 * 60             # BS-stuck-run: an expired claim/lease this long
REVIEW_IDLE_S = 60 * 60            # BS-review-no-reviewer
NOTICE_STALE_S = 2 * 3600          # BS-notice-undelivered: unclaimed notice, or deferred retry this far past due
NOTICE_LOOKBACK_S = 3 * 86400      # core notice_guarantee.LOOKBACK_S: older chats are out of the retry policy
SUB_LAG_S = 60 * 60                # BS-notice-undelivered: a subscription's oldest undelivered event
LONG_WAIT_S = {'ci': 45 * 60, 'deploy': 45 * 60, 'external': 3 * 3600,
               'card': 4 * 3600, 'resource': 2 * 3600}  # clock is owned by BS-overdue-wake
PRODUCER_QUIET_S = 3600
QUIET_CHAT_S = 3600
MAX_CHATS = 1000

RULES = ('BS-ready-worker-free', 'BS-overdue-wake', 'BS-unexplained-wait', 'BS-parent-done', 'BS-stuck-run',
         'BS-review-no-reviewer', 'BS-broken-chain', 'BS-notice-undelivered', 'BS-long-wait', 'BS-quiet-chat')
REPAIR_CHAT_TITLE = 'Session repair'
REPAIR_KEY_PREFIX = 'session-repair:'
READY_ENTRY_EVENTS = ('promoted', 'unblocked', 'continuation_woken', 'created', 'reclaimed', 'released', 'status',
                      'reopened', 'changes_requested', 'condition_met')
REVIEW_ENTRY_EVENTS = ('review_requested', 'hub_review_admitted', 'review_route_deferred', 'status',
                       'continuation_woken', 'condition_met')
NOTIFY_KINDS_FALLBACK = ('completed', 'blocked', 'gave_up', 'crashed', 'timed_out', 'status', 'archived', 'unblocked')


@dataclasses.dataclass
class Finding:
    rule: str
    card: Optional[str]
    board: Optional[str]
    chat: Optional[str]
    title: str
    summary: str
    evidence: dict
    fix: dict            # {kind: auto|card, action, commands: [...], why}
    variant: str = ''
    area: str = 'card'   # who acts on a fix card: 'hermes' (a Hermes machinery defect) or 'card' (the card's owner)

    @property
    def fingerprint(self) -> str:
        """rule + card (rule + chat for a chat-level finding): the dedupe key and the fix card's idempotency key."""
        target = f'{self.board}/{self.card}' if self.card else f'chat/{self.chat}'
        return f'{self.rule}:{target}'

    def to_dict(self) -> dict:
        return {'id': self.rule, 'variant': self.variant, 'card': self.card, 'board': self.board, 'chat': self.chat,
                'title': self.title, 'summary': self.summary, 'evidence': self.evidence, 'fix': self.fix,
                'area': self.area, 'fingerprint': self.fingerprint}


# ── loading the session-panel backend (its readers and chain walk) ─────────────────────────────────────────────────
def _panel_dir() -> Path:
    env = os.environ.get('SESSION_REPAIR_PANEL_DIR')
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / 'session-panel'


def ensure_core() -> None:
    """Make core importable when this runs outside a Hermes process (a cron script, a scratch copy): the
    `HERMES_CORE_DIR` env, else `$HERMES_HOME/hermes-agent`, as session-panel's tests do."""
    try:
        import hermes_cli  # noqa: F401
    except ImportError:
        home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')
        root = Path(os.environ.get('HERMES_CORE_DIR') or home / 'hermes-agent')
        if (root / 'hermes_cli' / 'kanban_db.py').is_file():
            sys.path.insert(0, str(root))


def load_panel():
    """(plugin_api, feed) modules of session-panel, loaded by path once per process (as its own tests do)."""
    ensure_core()
    key = '_session_repair_panel_api'
    if key in sys.modules:
        api = sys.modules[key]
    else:
        spec = importlib.util.spec_from_file_location(key, _panel_dir() / 'dashboard' / 'plugin_api.py')
        api = importlib.util.module_from_spec(spec)
        sys.modules[key] = api
        spec.loader.exec_module(api)
    return api, api._load_feed()


# ── small helpers ─────────────────────────────────────────────────────────────────────────────────────────────────
def _json(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, (str, bytes)) and v:
        try:
            return json.loads(v)
        except ValueError:
            return None
    return None


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _age(now: float, at: Optional[float]) -> Optional[int]:
    return int(now - at) if at is not None else None


def _fmt_age(s: Optional[float]) -> str:
    if s is None:
        return 'unknown'
    s = int(s)
    return f'{s // 3600}h {s % 3600 // 60}m' if s >= 3600 else f'{s // 60}m'


def kanban_cmd(board: str, verb: str, *args: str) -> str:
    b = f' --board {board}' if board and board != 'default' else ''
    return ' '.join([f'hermes kanban{b} {verb}', *args])


# ── sources ───────────────────────────────────────────────────────────────────────────────────────────────────────
class Sources:
    """The reads the rules need, all through the session-panel readers' query-only connections."""

    def __init__(self, readers: Any = None, *, now: Optional[float] = None,
                 exclude_chats: Optional[Iterable[str]] = None):
        api, feedmod = load_panel()
        self.api = api
        self.readers = readers if readers is not None else api.CoreReaders()
        self.feed = feedmod.Feed(self.readers, emit=lambda _payload: None)
        self.chain_refs = feedmod.chain_refs
        self.card_id = feedmod.CARD_ID
        self.now = time.time() if now is None else now
        self.errors: list[dict] = []
        self._own: dict[str, Optional[str]] = {}
        self._copies: dict[str, set] = {}
        self.exclude_chats = set(exclude_chats) if exclude_chats is not None else self.repair_chats()
        self._ours: Optional[set] = None

    def producer_state(self, kind: str, ref: str) -> dict:
        # Cache per tick: a chain can name the same producer many times.
        spec = importlib.util.spec_from_file_location('_session_repair_producer', Path(__file__).with_name('producer.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        read_github = mod.read_github
        cache = self.__dict__.setdefault('_producer_cache', {})
        key = (kind, ref)
        if key not in cache:
            if len(cache) >= 20:
                cache[key] = {'state':'readback_failed','happened':False,'reason':'per-scan producer read budget'}
            elif isinstance(ref, str) and ref.startswith('owner-target:'):
                target_spec = importlib.util.spec_from_file_location('_session_repair_targets', Path(__file__).with_name('target_reader.py'))
                targets = importlib.util.module_from_spec(target_spec)
                target_spec.loader.exec_module(targets)
                cache[key] = targets.read_target(ref, self.home() / 'session-repair' / 'target-readers.json', self.now)
            else:
                cache[key] = read_github(kind, ref)
        return cache[key]

    def outcome_rows(self) -> list[dict]:
        import subprocess
        r = subprocess.run(['hermes', 'sessions', 'outcome-audit', '--json', '--limit', str(MAX_CHATS)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode:
            raise RuntimeError('outcome-audit failed')
        rows = json.loads(r.stdout)
        if not isinstance(rows, list):
            raise ValueError('outcome-audit did not return a list')
        return rows

    def chat_obligations(self, sid: str) -> bool:
        # Canonical liveness/cron readers include compression ancestors and live writer leases.
        from hermes_state import SessionDB
        from hermes_state_outcome import _lineage_ids, _live_turn, _cron_items, _process_items, _subagent_items
        with SessionDB(self.readers.state_db(), read_only=True) as db:
            ids = _lineage_ids(db, sid)
            linked = self.linked_chats()
            if any(s in linked or _live_turn(db, s) for s in ids) or _cron_items(ids):
                return True
            if _process_items(sid, ids) or _subagent_items(db, ids):
                return True
            # Read canonical asks without importing plugin hooks or initializing their writable stores.
            asks = self.home() / 'user-steps' / 'actions.sqlite3'
            if asks.exists():
                c = self.readers.conn(asks)
                if c is None:
                    raise RuntimeError('open-ask store unreadable')
                marks = ','.join('?' for _ in ids)
                rows = c.execute(f"SELECT resolution FROM requests WHERE session IN ({marks}) "
                                 "AND state IN ('open','needs_context')", ids)
                if any(not _json(r[0]) for r in rows):
                    return True
            # Legacy asks can predate canonical request backfill; conservatively retain their human hold too.
            import re
            for ancestor in ids:
                legacy = self.home() / 'user-steps' / (re.sub(r'[^A-Za-z0-9_.-]', '_', ancestor) + '.json')
                if legacy.exists():
                    data = json.loads(legacy.read_text())
                    if any(s.get('status') == 'open' for s in data.get('steps', [])):
                        return True
            return False

    def home(self) -> Path:
        return Path(Path(self.readers.state_db()).parent)

    def tracked(self) -> dict[str, set[str]]:
        """{fingerprint: card or request ids} the repair turns CREATED (`filed` of ledger-live.jsonl rows whose outcome
        is `card` or `queued`). A card they only commented on, fixed or judged is not theirs and stays judged."""
        if getattr(self, '_tracked', None) is not None:
            return self._tracked
        out: dict[str, set[str]] = {}
        try:
            for line in (self.home() / 'session-repair' / 'ledger-live.jsonl').read_text().splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                filed = row.get('filed')
                if row.get('outcome') in ('card', 'queued') and isinstance(filed, str) and filed:
                    out.setdefault(str(row.get('fingerprint') or ''), set()).add(filed)
        except OSError:
            pass
        self._tracked = out
        return out

    def tracked_requests(self) -> set[str]:
        return set().union(set(), *self.tracked().values())

    def _intake(self) -> list[tuple[str, str, str, Optional[str]]]:
        """(board, request id, request idempotency key, created task id) of every self-work intake row."""
        rows = []
        for slug, _p in self.boards():
            c = self.conn(slug)
            try:
                if c is None or not c.execute("SELECT 1 FROM sqlite_master WHERE name='self_work_intake'").fetchone():
                    continue
                for rid, req, rec in c.execute('SELECT request_id, request_json, receipt_json FROM self_work_intake'):
                    key = str(((_json(req) or {}).get('args') or {}).get('idempotency_key') or '')
                    task = (_json(rec) or {}).get('task_id')
                    rows.append((slug, str(rid), key, str(task) if task else None))
            except Exception as e:  # noqa: BLE001
                self.error(f'self_work_intake {slug}', e)
        return rows

    def ours_ids(self) -> set[str]:
        """Cards the repair session created that do not carry its key: card ids its turns reported as `card`, and
        cards the self-work intake created for a request they reported as `queued` or whose request carried a
        `session-repair:` key (the intake's receipt names the task)."""
        if self._ours is not None:
            return self._ours
        tracked = self.tracked_requests()
        out = {t for t in tracked if t.startswith('t_')}
        for _slug, rid, key, task in self._intake():
            if task and (rid in tracked or key.startswith(REPAIR_KEY_PREFIX)):
                out.add(task)
        self._ours = out
        return out

    def _open(self, card: str) -> Optional[dict]:
        for slug, _p in self.boards():
            task, _c = self.task(slug, card)
            if task is not None:
                return {'id': card, 'board': slug, 'status': task.status} if task.status not in TERMINAL else None
        return None

    def open_fix_card(self, fingerprint: str) -> Optional[dict]:
        """The repair session's open fix card for this fingerprint (status not done/archived/cancelled), else None:
        a card whose idempotency key starts with `session-repair:<fingerprint>:` (any day bucket, any board; an
        index-friendly range, not LIKE: fingerprints hold `_`), or a card the turns reported creating for this
        fingerprint (directly, or through a queued self-work request the intake turned into a card)."""
        prefix = f'{REPAIR_KEY_PREFIX}{fingerprint}:'
        marks = ','.join('?' * len(TERMINAL))
        for slug, _p in self.boards():
            c = self.conn(slug)
            if c is None:
                continue
            try:
                r = c.execute(f'SELECT id, status FROM tasks WHERE idempotency_key >= ? AND idempotency_key < ? AND '
                              f'status NOT IN ({marks}) ORDER BY created_at DESC LIMIT 1',
                              (prefix, prefix + chr(0x10FFFF), *TERMINAL)).fetchone()
            except Exception as e:  # noqa: BLE001
                self.error(f'open_fix_card {slug}', e)
                continue
            if r is not None:
                return {'id': str(r[0]), 'board': slug, 'status': r[1]}
        ids = self.tracked().get(fingerprint) or set()
        if ids:
            created = {task: rid for _s, rid, _k, task in self._intake() if task and rid in ids}
            for card in sorted({i for i in ids if i.startswith('t_')} | set(created)):
                hit = self._open(card)
                if hit:
                    return hit
        return None

    def closeout_waking(self, board: str, card: str, premise: Optional[str]) -> Optional[dict]:
        """chat-closeout's owner wake already handled this (card, premise): `owner-wake.db` `fired` says a chat turn
        `started` or a supervisor was `routed` in the last 24 h, or a claim with no result yet is younger than
        chat-closeout's CLAIM_TTL_S (a wake in flight). Failed wakes (error, refused, busy) and abandoned claims do not
        count. Read-only."""
        if not premise:
            return None
        path = os.environ.get('CHAT_CLOSEOUT_OWNER_WAKE_DB')
        if not path:
            try:
                from hermes_cli import kanban_db
                root = Path(kanban_db.kanban_home())
            except Exception:  # noqa: BLE001
                root = self.home()
            path = str(root / 'cache' / 'chat-closeout' / 'owner-wake.db')
        if not Path(path).exists():
            return None
        c = self.readers.conn(Path(path))
        if c is None:
            return None
        try:
            r = c.execute('SELECT claimed_at, result, route FROM fired WHERE card_key = ? AND premise = ?',
                          (f'{board}/{card}', premise)).fetchone()
        except Exception as e:  # noqa: BLE001
            self.error('owner-wake.db', e)
            return None
        if r is None:
            return None
        claimed = _num(r[0]) or 0
        if r[1] in ('started', 'routed') and claimed >= self.now - 86400:
            return {'claimed_at': r[0], 'result': r[1], 'route': r[2]}
        if r[1] is None and claimed >= self.now - closeout_claim_ttl():
            return {'claimed_at': r[0], 'result': None, 'route': r[2]}  # a wake in flight
        return None  # too_old, error, refused, busy, or a claim its backend abandoned: nobody was woken

    def repair_chats(self) -> set[str]:
        """The Session repair chat(s): never scanned, never a notice target (`chat.json` from run.py, and any
        unarchived chat with that title in state.db)."""
        out: set[str] = set()
        try:
            home = Path(Path(self.readers.state_db()).parent)
            out.add(str(json.loads((home / 'session-repair' / 'chat.json').read_text())['session']))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        c = self.state_conn()
        if c is not None:
            try:
                out |= {str(r[0]) for r in c.execute('SELECT id FROM sessions WHERE title = ?', (REPAIR_CHAT_TITLE,))}
            except Exception as e:  # noqa: BLE001
                self.error('repair chat lookup', e)
        return out

    def copies(self, board: str) -> set:
        """Worker-node copies on this board (core `kanban_wake_check.foreign_ids`): their home board owns them."""
        if board not in self._copies:
            try:
                from hermes_cli.kanban_wake_check import foreign_ids
                self._copies[board] = set(foreign_ids(self.conn(board)))
            except Exception as e:  # noqa: BLE001
                self.error(f'foreign_ids {board}', e)
                self._copies[board] = set()
        return self._copies[board]

    def error(self, where: str, exc: BaseException) -> None:
        self.errors.append({'where': where, 'error': f'{type(exc).__name__}: {exc}'[:300]})
        log.debug('session-repair: %s failed', where, exc_info=True)

    def boards(self) -> list[tuple[str, Path]]:
        return [(slug, Path(p)) for slug, p in self.readers.boards() if Path(p).exists()]

    def conn(self, board: str):
        for slug, path in self.boards():
            if slug == board:
                return self.readers.conn(path)
        return None

    def state_conn(self):
        p = Path(self.readers.state_db())
        return self.readers.conn(p) if p.exists() else None

    def own_gateway(self, board: str) -> Optional[str]:
        if board not in self._own:
            try:
                from hermes_cli.kanban_wake_check import own_gateway
                self._own[board] = own_gateway(self.conn(board))
            except Exception as e:  # noqa: BLE001 -- unknown gateway: every chat link counts as ours
                self.error(f'own_gateway {board}', e)
                self._own[board] = None
        return self._own[board]

    def linked_chats(self) -> dict[str, set[tuple[str, str]]]:
        """{chat: {(board, card)}} for every open card linked to a chat of this gateway."""
        out: dict[str, set[tuple[str, str]]] = {}
        marks = ','.join('?' * len(TERMINAL))
        for slug, _path in self.boards():
            c = self.conn(slug)
            if c is None:
                continue
            try:
                for r in c.execute(f'SELECT id, session_id FROM tasks WHERE session_id IS NOT NULL AND status NOT IN ({marks})',
                                   TERMINAL):
                    out.setdefault(str(r[1]), set()).add((slug, str(r[0])))
                own = self.own_gateway(slug)
                has_links = c.execute("SELECT 1 FROM sqlite_master WHERE name='task_chat_links'").fetchone()
                if has_links:
                    rows = c.execute(f'SELECT l.task_id, l.chat_session, l.chat_gateway FROM task_chat_links l JOIN tasks t '
                                     f'ON t.id = l.task_id WHERE l.released = 0 AND t.status NOT IN ({marks})', TERMINAL)
                    for r in rows:
                        if own and r[2] and r[2] != own:
                            continue
                        out.setdefault(str(r[1]), set()).add((slug, str(r[0])))
            except Exception as e:  # noqa: BLE001 -- one unreadable board never stops the scan
                self.error(f'linked_chats {slug}', e)
        return out

    def bundle(self, chat: str) -> Optional[dict]:
        try:
            return self.feed.compute(chat)
        except Exception as e:  # noqa: BLE001
            self.error(f'bundle {chat}', e)
            return None

    def task(self, board: str, card: str):
        """(kanban_db.Task or None, conn) on this gateway's live board file."""
        c = self.conn(board)
        if c is None:
            return None, None
        try:
            from hermes_cli import kanban_db
            return kanban_db.get_task(c, card), c
        except Exception as e:  # noqa: BLE001
            self.error(f'task {board}/{card}', e)
            return None, c

    def find_card(self, card: str, prefer: str) -> tuple[Optional[str], Optional[dict]]:
        """(board, {status, completed_at, title}) for a card on any live board or replica, else (None, None)."""
        try:
            rec = self.readers.card(card, prefer)
        except Exception as e:  # noqa: BLE001
            self.error(f'card {card}', e)
            rec = None
        if rec is None:
            for slug, _p in self.boards():
                if slug == prefer:
                    continue
                try:
                    rec = self.readers.card(card, slug)
                except Exception:  # noqa: BLE001
                    rec = None
                if rec is not None:
                    break
        if rec is None:
            return None, None
        t = (rec.get('detail') or {}).get('task') or {}
        return rec.get('board'), {'status': t.get('status'), 'completed_at': t.get('completed_at'),
                                  'title': t.get('title'), 'source': (rec.get('detail') or {}).get('source')}


# ── per-card reads ─────────────────────────────────────────────────────────────────────────────────────────────────
def _events(conn, card: str, kinds: Iterable[str] = (), limit: int = 1) -> list[dict]:
    kinds = tuple(kinds)
    sql = 'SELECT id, kind, payload, created_at, run_id FROM task_events WHERE task_id = ?'
    args: list[Any] = [card]
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += kinds
    sql += ' ORDER BY id DESC LIMIT ?'
    args.append(limit)
    return [{'id': r[0], 'kind': r[1], 'payload': _json(r[2]) or {}, 'at': r[3], 'run_id': r[4]}
            for r in conn.execute(sql, args)]


def _last_event_at(conn, card: str) -> Optional[float]:
    row = conn.execute('SELECT MAX(created_at) FROM task_events WHERE task_id = ?', (card,)).fetchone()
    return _num(row[0]) if row else None


def _col(conn, table: str, col: str) -> bool:
    return any(r[1] == col for r in conn.execute(f'PRAGMA table_info({table})'))


@dataclasses.dataclass
class Card:
    id: str
    board: str
    chat: Optional[str]
    task: Any                 # kanban_db.Task
    conn: Any
    row: Optional[dict]       # the chat's session-tasks row (has `wait`), when the card is linked to it
    via: Optional[str] = None  # the linked card whose chain reached this one

    @property
    def status(self) -> str:
        return str(getattr(self.task, 'status', '') or '')

    @property
    def title(self) -> str:
        return str(getattr(self.task, 'title', '') or '')

    @property
    def ours(self) -> bool:
        """A card the repair session itself filed (its idempotency key): never judged, so no repair loop."""
        return str(getattr(self.task, 'idempotency_key', '') or '').startswith(REPAIR_KEY_PREFIX)

    def base(self) -> dict:
        ev = {'status': self.status}
        if self.via:
            ev['reached_via'] = self.via
        return ev


Rule = Callable[[Card, Sources], Optional[Finding]]


def _finding(rule: str, card: Card, variant: str, summary: str, evidence: dict, fix: dict,
             area: str = 'card') -> Finding:
    return Finding(rule=rule, card=card.id, board=card.board, chat=card.chat, title=card.title, summary=summary,
                   evidence={**card.base(), **evidence}, fix=fix, variant=variant, area=area)


def closeout_claim_ttl() -> float:
    """chat-closeout's owner-wake CLAIM_TTL_S, read from its source (no import: its module has side effects on
    load), else 600 s."""
    import re
    try:
        text = (Path(__file__).resolve().parent.parent / 'chat-closeout' / 'owner_wake.py').read_text()
        m = re.search(r'^CLAIM_TTL_S\s*=\s*([0-9.]+)', text, re.M)
        return float(m.group(1)) if m else 600.0
    except OSError:
        return 600.0


def _core_wait():
    """Core's wait vocabulary (kanban_wait_info): park events and agent-owned block kinds, never local copies."""
    from hermes_cli import kanban_wait_info as kwi
    return kwi.PARK_EVENT_KINDS, kwi.AGENT_BLOCK_KINDS


# ── rules ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _machine_free(place: dict, src: Sources) -> tuple[Optional[str], dict]:
    """For a card waiting on other machines (`lane: machine`): the fitting machines that are active, unheld, not
    draining, seen within core's freshness window and report free card slots (`max_card_runs - active_card_runs`,
    core `kanban_pickup_stall._free_slots`), when those slots outnumber the cards ranked ahead of it on its labels.
    None when any of that is unknown: an unknown registry or capacity is never "free"."""
    try:
        from hermes_cli import kanban_pickup_stall as ps
        from hermes_cli import kanban_worker_node as kwn
        machines = (kwn.registry() or {}).get('machines') or {}
    except Exception as e:  # noqa: BLE001
        src.error('machine registry', e)
        return None, {}
    skip = set(place.get('held') or ()) | set(place.get('draining') or ())
    now_ms = int(src.now * 1000)
    free, names, slots_by = 0, [], {}
    for name in place.get('machines') or ():
        entry = machines.get(name)
        if name in skip or not entry or entry.get('state', 'active') != 'active' or entry.get('hold'):
            continue
        try:
            slots = ps._free_slots(entry) if ps._live(entry, now_ms) else None
        except Exception:  # noqa: BLE001
            slots = None
        if isinstance(slots, int) and slots > 0:
            free += slots
            slots_by[name] = slots
            names.append(f'{name} ({slots} free)')
    ahead = (place.get('backlog') or {}).get('ahead') or 0
    ahead = ahead if isinstance(ahead, int) else 0
    if free > ahead:
        return f'{", ".join(names)} with {ahead} card(s) ahead on its labels', slots_by
    return None, {}


def _ready_since(c, card: Card) -> Optional[float]:
    ev = _events(c, card.id, READY_ENTRY_EVENTS)
    entered = _num(ev[0]['at']) if ev else None
    if _col(c, 'tasks', 'ready_since'):
        rs = c.execute('SELECT ready_since FROM tasks WHERE id = ?', (card.id,)).fetchone()
        since = _num(rs[0]) if rs else None
        if since:
            return max(since / 1000.0, entered or 0)
    return entered if entered is not None else _num(getattr(card.task, 'created_at', None))


def _current_idle_status(card: Card, status: str) -> bool:
    """Task objects can precede a concurrent admission/park; do not diagnose that stale episode."""
    row = card.conn.execute('SELECT status FROM tasks WHERE id=?', (card.id,)).fetchone()
    return bool(row and row[0] == status and not card.conn.execute(
        "SELECT 1 FROM task_runs WHERE task_id=? AND status='running' LIMIT 1", (card.id,)).fetchone())


def _skip_explains(skip: Any, now: float) -> bool:
    """A recorded skip explains a ready wait, however old, unless it is a timed hold whose `until` has passed
    (core's diagnostics drop such a hold too). `until` in seconds; a millisecond stamp is read as such."""
    if not skip:
        return False
    until = _num(skip.get('until')) if isinstance(skip, dict) else None
    if until is None:
        return True
    if until > 1e12:
        until /= 1000.0
    return until > now


def rule_ready_worker_free(card: Card, src: Sources) -> Optional[Finding]:
    """BS-ready-worker-free: ready and unclaimed while a worker that can run it has a free slot, with no recorded
    dispatcher skip in the current ready episode (a skip of any age, or one with `until`, explains the wait)."""
    if card.status != 'ready' or not _current_idle_status(card, 'ready'):
        return None
    c, now = card.conn, src.now
    waited = _age(now, _ready_since(c, card))
    if waited is None:
        return None
    try:
        from hermes_cli import kanban_dispatch_skip as kds
        from hermes_cli import kanban_queue_position as kqp
        place = kqp.read(c, card.task, card.board) or {}
        note = kqp.describe(place) if place else None
    except Exception as e:  # noqa: BLE001
        src.error(f'queue {card.board}/{card.id}', e)
        return None
    lane = place.get('lane')
    ev = {'ready_for_s': waited, 'lane': lane, 'queue_note': note}
    if lane == 'leftover_claim':
        if waited < READY_UNCLAIMED_S:
            return None
        return _finding('BS-ready-worker-free', card, 'leftover_claim',
                        f'ready {_fmt_age(waited)} but still holds claim {place.get("claim_lock")!r}: the dispatcher '
                        f'never picks a claimed card', {**ev, 'claim_lock': place.get('claim_lock'),
                                                        'worker_pid': place.get('worker_pid')},
                        {'kind': 'card', 'action': 'clear the leftover claim so the dispatcher can pick the card',
                         'commands': [kanban_cmd(card.board, 'show', card.id)],
                         'why': 'releasing a claim on a ready card is not one of the automatic fixes'}, 'hermes')
    try:  # the queue view carries `skip` on the local lane only; ask core directly for the other lanes
        skip = place.get('skip') or kds.latest(c, card.id)
    except Exception as e:  # noqa: BLE001
        src.error(f'dispatch skip {card.board}/{card.id}', e)
        skip = None
    if _skip_explains(skip, now):
        return None  # the current ready episode has a recorded skip: the wait is explained, however old
    if place.get('pool_could_run'):
        if waited < READY_UNCLAIMED_S:
            return None
        pool = ', '.join(map(str, place['pool_could_run']))
        return _finding('BS-ready-worker-free', card, 'pool_could_run',
                        f'ready {_fmt_age(waited)} for a host slot while pool workers could run it now: {pool}',
                        {**ev, 'pool_could_run': place['pool_could_run'], 'running': place.get('running'),
                         'limit': place.get('limit')},
                        {'kind': 'card', 'action': 're-place the card on the pool (hermes kanban move) or fix why it '
                         'is held home-only', 'commands': [kanban_cmd(card.board, 'show', card.id),
                                                           kanban_cmd(card.board, 'move', card.id, '--help')],
                         'why': 'a dispatcher pass here only uses host slots; the pool needs a re-placement'}, 'hermes')
    if lane == 'local':
        # core: >= 30 min, running < limit, no recorded skip (an expired timed hold counts as none)
        if not kds.unrecorded({**place, 'skip': None}, waited):
            return None
        return _finding('BS-ready-worker-free', card, 'free_slot',
                        f'ready {_fmt_age(waited)}, unclaimed, with no recorded skip, while the host runs '
                        f'{place.get("running")} of {place.get("limit")} workers',
                        {**ev, 'running': place.get('running'), 'limit': place.get('limit'), 'ahead': place.get('ahead')},
                        {'kind': 'auto', 'action': 're-run one dispatcher pass on the board',
                         'commands': [kanban_cmd(card.board, 'dispatch', '--max', '1', '--json')],
                         'why': 'one pass claims ready cards by priority, or records why it skipped them'}, 'hermes')
    if lane == 'machine' and waited >= READY_UNCLAIMED_S:
        free, free_machines = _machine_free(place, src)
        if free:
            return _finding('BS-ready-worker-free', card, 'free_worker',
                            f'ready {_fmt_age(waited)}, unclaimed, while {free}', {**ev, 'free': free,
                                                                                  'free_machines': free_machines},
                            {'kind': 'card', 'action': "find why the free worker does not lease it (the worker's "
                             "pickup skips: `kanban_show` there, skill hermes-workers), fix that, or re-place the card",
                             'commands': [kanban_cmd(card.board, 'show', card.id)],
                             'why': 'a dispatcher pass here cannot start work on another machine'}, 'hermes')
    return None


def rule_overdue_wake(card: Card, src: Sources) -> Optional[Finding]:
    """BS-overdue-wake: scheduled, wake > 30 min past due (clock or probe), or no wake published for > 30 min."""
    if card.status != 'scheduled':
        return None
    c, now = card.conn, src.now
    try:
        from hermes_cli import kanban_continuation as kc
        park = kc.park_state(c, card.id)
        # Core reserves a final authorized run for the parked condition. A
        # generic timer/probe-age repair must not spend that run or override it.
        if kc._reserved_for(c, card.id, park):
            return None
    except Exception as e:  # noqa: BLE001
        src.error(f'park {card.board}/{card.id}', e)
        return None
    if park.get('timed'):
        wake_at = _num((park.get('payload') or {}).get('wake_at'))
        late = _age(now, wake_at)
        if late is not None and late > WAKE_OVERDUE_S:
            return _finding('BS-overdue-wake', card, 'clock_overdue', f'clock wake {_fmt_age(late)} past due',
                            {'wake_at': wake_at, 'late_s': late, 'parked_at': park.get('parked_at')},
                            {'kind': 'auto', 'action': 'wake the park now (scheduled -> ready, or todo while a parent '
                             'is open)', 'commands': [kanban_cmd(card.board, 'unblock', card.id)],
                             'why': 'the clock wake is past due; unblock is what the wake itself would have done'},
                            'hermes')
        return None
    probe = None
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='task_wake_checks'").fetchone():
        has_purpose = _col(c, 'task_wake_checks', 'purpose')
        probe = c.execute('SELECT cmd, next_at, last_at, last_rc, fails FROM task_wake_checks WHERE task_id = ?'
                          + (" AND purpose = 'wake'" if has_purpose else ''), (card.id,)).fetchone()
    if probe is not None:
        late = _age(now, _num(probe[1]))
        if late is not None and late > WAKE_OVERDUE_S:
            return _finding('BS-overdue-wake', card, 'probe_overdue', f'wake probe {_fmt_age(late)} past its next run',
                            {'probe_cmd': str(probe[0])[:300], 'probe_next_at': probe[1], 'probe_last_at': probe[2],
                             'probe_last_rc': probe[3], 'probe_fails': probe[4], 'late_s': late},
                            {'kind': 'card', 'action': 'run the probe command (evidence.probe_cmd) by hand first: '
                             'exit 0 = its condition holds, then unblock the card; otherwise leave the park and fix '
                             'the stuck probe runner', 'commands': [str(probe[0])[:300],
                                                                    kanban_cmd(card.board, 'unblock', card.id)],
                             'why': 'waking a park whose condition is not met restarts work early; the probe runner '
                             'being this late is a gateway defect'}, 'hermes')
        return None
    parked = _age(now, _num(park.get('parked_at')))
    if parked is not None and parked > WAKE_OVERDUE_S:
        missing = None
        try:
            missing = kc.missing_wake(c, card.id, park=park)
        except Exception as e:  # noqa: BLE001
            src.error(f'missing_wake {card.board}/{card.id}', e)
        # A superseded park (its own newer park is still on the way) has no wake yet by design:
        # core backstops it at parked_at + SUPERSEDED_PARK_GRACE_SECONDS. Never call the card
        # unwoken while that backstop is pending; core's own late-wake grace applies after it.
        implied = _num((missing or {}).get('wake_at'))
        if park.get('superseded') and implied is not None and now - implied <= WAKE_OVERDUE_S:
            return None
        return _finding('BS-overdue-wake', card, 'no_wake',
                        f'scheduled {_fmt_age(parked)} with no wake published (no wake_at, no probe)',
                        {'parked_s': parked, 'park_superseded': bool(park.get('superseded')),
                         'implied_wake_at': (missing or {}).get('wake_at'),
                         'park_reason': str((park.get('payload') or {}).get('reason') or '')[:300]},
                        {'kind': 'card', 'action': "find what the park waits on and re-park it with a real wake "
                         "(`kanban_schedule` with awaiting/wake_at/wake_check), or unblock it",
                         'commands': [kanban_cmd(card.board, 'show', card.id)],
                         'why': 'only the card\'s owner knows what it waits for'})
    return None


def _human_request(task) -> tuple[Any, bool]:
    raw = getattr(task, 'human_request', None)
    if raw in (None, '', '{}', 'null'):
        return None, False
    value = _json(raw) if isinstance(raw, str) else raw
    try:
        from hermes_cli.kanban_human_request import needs_human
        return value, bool(needs_human(value, kind=getattr(task, 'block_kind', None)))
    except Exception:  # noqa: BLE001 -- an unreadable validator: treat the ask as valid (never a false alarm)
        return value, True


def _park_reason(payload: dict) -> str:
    return str(payload.get('reason') or payload.get('error') or '')


def rule_unexplained_wait(card: Card, src: Sources) -> Optional[Finding]:
    """BS-unexplained-wait: blocked with no reason, an invalid human_request, or an agent-owned block > 2 h."""
    if card.status not in ('blocked', 'triage'):
        return None
    c, now = card.conn, src.now
    park_kinds, agent_kinds = _core_wait()
    kind = getattr(card.task, 'block_kind', None)
    hr, valid = _human_request(card.task)
    if hr is not None and valid:
        return None  # a real ask: the chat is red, the user acts
    # Spend triage is a deliberate decision hold, not permission to clear an
    # inherited agent-owned block. Use the canonical reader, not comment text
    # matching (a retained HOLD may also carry a monotone scope restriction).
    from hermes_cli import kanban_spend_decision as spend
    from hermes_cli import kanban_containment_connections as containment
    if containment.held(c, card.id) or spend.pending(c, card.id) or spend.execution_refusal(c, card.id):
        return None
    park = _events(c, card.id, park_kinds)
    # Core's projected wait can predate the current run: its generic park
    # vocabulary omits spend triage and historically selected an old schedule.
    # Never date a new blocked/triage episode before its latest admission.
    row = c.execute('SELECT MAX(started_at), MAX(ended_at) FROM task_runs WHERE task_id = ?',
                    (card.id,)).fetchone()
    admitted = _num(row[0]) if row else None
    ended = _num(row[1]) if row else None
    boundary = max(t for t in (admitted, ended) if t is not None) if any(
        t is not None for t in (admitted, ended)) else None
    if park and admitted is not None and (_num(park[0]['at']) or 0) < admitted:
        park = []
    park_since = _num(park[0]['at']) if park else None
    since = max(t for t in (park_since, boundary) if t is not None) if any(
        t is not None for t in (park_since, boundary)) else None
    if since is None:
        since = _last_event_at(c, card.id)
    waited = _age(now, since)
    reason = _park_reason(park[0]['payload'] or {}) if park else ''
    if hr is not None and not valid:
        return _finding('BS-unexplained-wait', card, 'invalid_human_request',
                        f'{card.status} on a human_request core does not accept (the ask is invisible to you)',
                        {'block_kind': kind, 'human_request': hr if len(json.dumps(hr, default=str)) < 1500 else '…',
                         'waiting_s': waited},
                        {'kind': 'card', 'action': 're-file the ask with a valid human_request (action, why_human, '
                         'interaction, scope, evidence), or clear the block if no person is needed',
                         'commands': [kanban_cmd(card.board, 'show', card.id)],
                         'why': 'an invalid ask never reaches Needs you; only the owner can restate it'})
    if card.status == 'blocked' and kind is None and not reason.strip():
        if waited is not None and waited >= NO_REASON_S:
            return _finding('BS-unexplained-wait', card, 'no_reason', f'blocked {_fmt_age(waited)} with no reason given',
                            {'waiting_s': waited},
                            {'kind': 'card', 'action': 'ask the owning chat why it blocked; unblock it or block again '
                             'with a typed kind and reason', 'commands': [kanban_cmd(card.board, 'show', card.id)],
                             'why': 'nobody can act on a block with no reason'})
        return None
    wait = (card.row or {}).get('wait') or {}
    agent = wait.get('agent_owned') if isinstance(wait, dict) and 'agent_owned' in wait else (kind in agent_kinds)
    since_w = _num(wait.get('waiting_since')) if isinstance(wait, dict) else None
    # A display projection is advisory; it cannot backdate this episode.
    effective_since = max(t for t in (since, since_w) if t is not None) if any(
        t is not None for t in (since, since_w)) else None
    waited_w = _age(now, effective_since)
    if agent and waited_w is not None and waited_w > AGENT_BLOCK_S:
        handled = src.closeout_waking(card.board, card.id, wait.get('premise') if isinstance(wait, dict) else None)
        if handled:
            return None  # chat-closeout's owner wake already woke its chat for this very premise
        return _finding('BS-unexplained-wait', card, 'agent_owned_stalled',
                        f'{card.status} {_fmt_age(waited_w)} on an agent-owned wait '
                        f'({wait.get("waiting_on") or kind or "untyped block"}) nobody is working',
                        {'block_kind': kind, 'waiting_on': wait.get('waiting_on'), 'waiting_s': waited_w,
                         'reason': reason[:300]},
                        {'kind': 'card', 'action': 'an agent clears the block (or turns it into a real ask)',
                         'commands': [kanban_cmd(card.board, 'show', card.id)],
                         'why': 'agent-owned blocks have no producer: they wait until an agent acts'})
    return None


def _parents(c, card: str) -> list[str]:
    return [str(r[0]) for r in c.execute('SELECT parent_id FROM task_links WHERE child_id = ? ORDER BY parent_id', (card,))]


def _remote_parent(c, pid: str) -> Optional[dict]:
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='kanban_sync_remote_parents'").fetchone():
        return None
    r = c.execute('SELECT status, refreshed_at FROM kanban_sync_remote_parents WHERE parent_id = ?', (pid,)).fetchone()
    return {'status': r[0], 'refreshed_at': r[1]} if r else None


def _parent_state(c, pid: str) -> Optional[dict]:
    from hermes_cli import kanban_db
    p = kanban_db.get_task(c, pid)
    if p is not None:
        return {'status': p.status, 'completed_at': _num(getattr(p, 'completed_at', None)) or _last_event_at(c, pid),
                'where': 'board'}
    rp = _remote_parent(c, pid)
    if rp is not None:
        return {'status': rp['status'], 'completed_at': _num(rp['refreshed_at']), 'where': 'remote'}
    return None


def _awaited_card(c, card: str) -> Optional[dict]:
    """The card ref of the newest park's `awaiting` (kind card), or None."""
    try:
        from hermes_cli import kanban_continuation as kc
        from hermes_cli.kanban_wait_info import parse_awaiting
        park = kc.park_state(c, card)
    except Exception:  # noqa: BLE001
        return None
    awaited = parse_awaiting((park.get('payload') or {}).get('awaiting'))
    if awaited and awaited['kind'] == 'card' and awaited['ref']:
        return awaited
    return None


def _failure_limit(kbd) -> int:
    """`kanban.failure_limit` from config, else core's default (the breaker recovery_held compares against)."""
    try:
        from hermes_cli.config import load_config_readonly
        value = int(((load_config_readonly() or {}).get('kanban') or {}).get('failure_limit'))
        return value if value > 0 else kbd.DEFAULT_FAILURE_LIMIT
    except (TypeError, ValueError, ImportError):
        return kbd.DEFAULT_FAILURE_LIMIT


def _promotion_held(c, card: Card) -> Optional[str]:
    """Why core's recompute_ready deliberately leaves this card where it is (kanban_db.recompute_ready's holds), else
    None: containment, a pending spend decision (triage), or a sticky stop / failure breaker (`recovery_held`)."""
    from hermes_cli import kanban_containment_connections as containment
    from hermes_cli import kanban_db_dispatch as kbd
    from hermes_cli import kanban_spend_decision as spend
    from hermes_cli.kanban_block_routing import recovery_held
    if containment.held(c, card.id):
        return 'containment'
    if card.status == 'triage' and spend.pending(c, card.id):
        return 'spend_decision'
    row = c.execute('SELECT id, status, consecutive_failures, max_retries FROM tasks WHERE id = ?', (card.id,)).fetchone()
    if row is not None and recovery_held(c, row, _failure_limit(kbd)):
        return 'recovery_held'
    return None


def rule_parent_done(card: Card, src: Sources) -> Optional[Finding]:
    """BS-parent-done: still waiting on parents (core's promotion set: done/archived) or an awaited card that
    finished > 10 min ago, with none of core's deliberate holds."""
    c, now = card.conn, src.now
    if card.status == 'todo' or (card.status == 'blocked' and getattr(card.task, 'block_kind', None) == 'dependency'):
        parents = _parents(c, card.id)
        if not parents:
            return None
        try:
            from hermes_cli import kanban_db
            from hermes_cli import kanban_db_sync as sync
            remote = sync.has_remote_parents(c)
            states = dict(kanban_db.parent_states(c, card.id, remote=remote))
            # Terminal status does not release a parent's independent execution
            # restriction. Reuse core's actual dependency predicate, not prose.
            blocked_parents = any(kanban_db.parent_blocks(c, pid, st) for pid, st in states.items())
            held = _promotion_held(c, card)
        except Exception as e:  # noqa: BLE001
            src.error(f'parent_states {card.board}/{card.id}', e)
            return None
        if held or blocked_parents or not states or any(st not in SATISFIED for st in states.values()):
            return None
        times = [(_parent_state(c, p) or {}).get('completed_at') for p in states]
        last = max((t for t in times if t is not None), default=None)
        since = _age(now, last)
        if since is None or since < PARENT_DONE_S:
            return None
        return _finding('BS-parent-done', card, 'parents_done',
                        f'{card.status} {_fmt_age(since)} after its last parent finished; core did not promote it',
                        {'parents': states, 'since_last_parent_s': since},
                        {'kind': 'card', 'action': "find why recompute_ready skipped it (the dispatcher's promotion "
                         "pass), fix that; promote by hand only with that cause named",
                         'commands': [kanban_cmd(card.board, 'show', card.id), kanban_cmd(card.board, 'promote', card.id)],
                         'why': 'core promotes such a card on its own unless it holds it on purpose; a residual case '
                         'is a promotion defect, not something to paper over'}, 'hermes')
    if card.status == 'scheduled':
        awaited = _awaited_card(c, card.id)
        if not awaited:
            return None
        ref = awaited['ref']
        board, state = src.find_card(ref, card.board)
        if not state or state['status'] not in SATISFIED:
            return None
        since = _age(now, _num(state.get('completed_at')))
        if since is None or since < PARENT_DONE_S:
            return None
        return _finding('BS-parent-done', card, 'awaited_done',
                        f'parked on card {ref}, which is {state["status"]} for {_fmt_age(since)}',
                        {'awaited': ref, 'awaited_board': board, 'awaited_status': state['status'],
                         'since_s': since},
                        {'kind': 'auto', 'action': 'wake the park: the card it awaits has finished',
                         'commands': [kanban_cmd(card.board, 'unblock', card.id)],
                         'why': 'the awaited-card wake did not fire'}, 'hermes')
    return None


def rule_stuck_run(card: Card, src: Sources) -> Optional[Finding]:
    """BS-stuck-run: running with no event for > 60 min, or its worker claim / lease is gone."""
    if card.status != 'running':
        return None
    c, now, t = card.conn, src.now, card.task
    run = None
    rid = getattr(t, 'current_run_id', None)
    if rid:
        r = c.execute('SELECT status, started_at, last_heartbeat_at, profile FROM task_runs WHERE id = ?', (rid,)).fetchone()
        run = {'id': rid, 'status': r[0], 'started_at': r[1], 'last_heartbeat_at': r[2], 'profile': r[3]} if r else None
    marks = [_last_event_at(c, card.id), _num(getattr(t, 'last_heartbeat_at', None)),
             _num((run or {}).get('last_heartbeat_at')), _num((run or {}).get('started_at')),
             _num(getattr(t, 'started_at', None))]
    last = max((m for m in marks if m is not None), default=None)
    quiet = _age(now, last)
    claim_lock, claim_expires = getattr(t, 'claim_lock', None), _num(getattr(t, 'claim_expires', None))
    lease_holder, lease_expires = None, None
    if _col(c, 'tasks', 'lease_holder'):
        lr = c.execute('SELECT lease_holder, lease_expires FROM tasks WHERE id = ?', (card.id,)).fetchone()
        lease_holder, lease_expires = (lr[0], _num(lr[1])) if lr else (None, None)
    ev = {'quiet_s': quiet, 'run': run, 'claim_lock': claim_lock, 'claim_expires': claim_expires,
          'lease_holder': lease_holder, 'lease_expires_ms': lease_expires, 'worker_pid': getattr(t, 'worker_pid', None)}
    fix = {'kind': 'card', 'action': 'check the worker (log, process, lease holder); reclaim the card if the worker '
           'is gone, or let it finish if it is alive and quiet',
           'commands': [kanban_cmd(card.board, 'log', card.id), kanban_cmd(card.board, 'runs', card.id),
                        kanban_cmd(card.board, 'reclaim', card.id)],
           'why': 'reclaiming a live worker\'s card loses its work: a person or agent must look first'}
    variant = None
    if claim_lock and claim_expires is not None and now - claim_expires > CLAIM_GRACE_S:
        variant, summary = 'claim_expired', f'running, its claim expired {_fmt_age(now - claim_expires)} ago'
    elif lease_holder and lease_expires is not None and now - lease_expires / 1000.0 > CLAIM_GRACE_S:
        variant, summary = 'lease_expired', (f'running on {lease_holder}, whose lease expired '
                                             f'{_fmt_age(now - lease_expires / 1000.0)} ago')
    elif not claim_lock and not lease_holder and not rid:
        variant, summary = 'no_claim', 'running with no claim, no lease and no current run'
    elif quiet is not None and quiet > RUN_QUIET_S:
        variant, summary = 'quiet', f'running, no event (not even a heartbeat) for {_fmt_age(quiet)}'
    if variant is None:
        return None
    return _finding('BS-stuck-run', card, variant, summary, ev, fix, 'hermes')


def rule_review_no_reviewer(card: Card, src: Sources) -> Optional[Finding]:
    """BS-review-no-reviewer: in review with no review run for > 60 min."""
    if card.status != 'review' or not _current_idle_status(card, 'review'):
        return None
    c, now = card.conn, src.now
    rid = getattr(card.task, 'current_run_id', None)
    if rid:
        r = c.execute('SELECT status FROM task_runs WHERE id = ?', (rid,)).fetchone()
        if r and r[0] == 'running':
            return None
    ev = _events(c, card.id, REVIEW_ENTRY_EVENTS)
    since = _num(ev[0]['at']) if ev else _last_event_at(c, card.id)
    idle = _age(now, since)
    if idle is None or idle < REVIEW_IDLE_S:
        return None
    try:
        from hermes_cli.kanban_db_dispatch import review_dispatch_enabled
        auto = review_dispatch_enabled()
    except Exception:  # noqa: BLE001
        auto = None
    if auto:
        fix = {'kind': 'auto', 'action': 're-run one dispatcher pass: review dispatch is on, so it starts a reviewer',
               'commands': [kanban_cmd(card.board, 'dispatch', '--max', '1', '--json')],
               'why': 'review runs are dispatched like ready cards'}
    else:
        fix = {'kind': 'card', 'action': 'start a review (review dispatch is off on this gateway) or send it back',
               'commands': [kanban_cmd(card.board, 'show', card.id)], 'why': 'no automatic reviewer will come'}
    return _finding('BS-review-no-reviewer', card, 'idle' if auto else 'idle_manual_review',
                    f'in review {_fmt_age(idle)} with no review run',
                    {'idle_s': idle, 'review_dispatch': auto, 'last_review_event': (ev[0]['kind'] if ev else None)},
                    fix, 'hermes' if auto else 'card')


def _cycle(c, start: str, limit: int = 60) -> Optional[list[str]]:
    """A dependency cycle through `start` over parent links, as the path, else None."""
    path: list[str] = []
    seen: set[str] = set()

    def walk(node: str, depth: int) -> Optional[list[str]]:
        if depth > limit:
            return None
        if node in path:
            return path[path.index(node):] + [node]
        if node in seen:
            return None
        seen.add(node)
        path.append(node)
        for p in _parents(c, node):
            hit = walk(p, depth + 1)
            if hit:
                return hit
        path.pop()
        return None
    hit = walk(start, 0)
    return hit if hit and start in hit else None


def rule_broken_chain(card: Card, src: Sources) -> Optional[Finding]:
    """BS-broken-chain: waiting on a card no board has, or a dependency cycle."""
    if card.status in TERMINAL or card.status == 'running':
        return None
    c = card.conn
    fix = {'kind': 'card', 'action': 'relink the card to the right parent (or unlink the missing one) so it can run',
           'commands': [kanban_cmd(card.board, 'show', card.id), kanban_cmd(card.board, 'unlink', '<parent>', card.id)],
           'why': 'graph edits change what the card waits for: its owner decides'}
    missing = [p for p in _parents(c, card.id) if _parent_state(c, p) is None and src.find_card(p, card.board)[1] is None]
    if missing:
        return _finding('BS-broken-chain', card, 'missing_parent', f'waits on parent card(s) no board has: {", ".join(missing)}',
                        {'missing': missing}, fix)
    if card.status == 'scheduled':
        awaited = _awaited_card(c, card.id)
        if awaited and not awaited.get('gateway') and src.find_card(awaited['ref'], card.board)[1] is None:
            return _finding('BS-broken-chain', card, 'missing_awaited',
                            f'parked on card {awaited["ref"]}, which no board here has',
                            {'missing': [awaited['ref']]},
                            {**fix, 'action': 're-park on the right card (or a real wake)'})
    loop = _cycle(c, card.id)
    if loop:
        return _finding('BS-broken-chain', card, 'cycle', 'dependency cycle: ' + ' -> '.join(loop), {'cycle': loop}, fix)
    return None


def current_park(card: Card) -> Optional[dict]:
    """Newest core park in this episode, fenced by the latest admitted run."""
    park = _events(card.conn, card.id, _core_wait()[0])
    if not park:
        return None
    row = card.conn.execute('SELECT MAX(started_at) FROM task_runs WHERE task_id=?', (card.id,)).fetchone()
    if row and row[0] is not None and park[0]['at'] < row[0]:
        return None
    return park[0]


def wait_producer(card: Card, park: dict, now: float) -> tuple[Optional[dict], dict]:
    """Structured producer, else core's legacy wait/probe view. A probe's backstop is not a clock."""
    from hermes_cli.kanban_wait_info import describe, parse_awaiting, producer_from_wake_check
    payload = park['payload']
    wait = (card.row or {}).get('wait') or describe(
        card.status, block_kind=getattr(card.task, 'block_kind', None),
        park=payload, park_kind=park['kind'], since=park['at'], now=now) or {}
    awaited = parse_awaiting(payload.get('awaiting'))
    if awaited:
        return awaited, wait  # explicit clock/human declarations must never be reinterpreted
    probe = producer_from_wake_check(payload.get('wake_check'))
    if not probe or wait.get('wait_state') == 'human':
        return None, wait
    label = str(wait.get('waiting_on') or '')
    if probe[0] == 'card':
        return {'kind':'card', 'ref':probe[1]}, wait
    # Core versions describe opaque probes differently. Only the fallback with an actual
    # wake_check is eligible; free text alone does not grant an early clock wake.
    text = label + ' ' + str(payload.get('reason') or '')
    kind = ('deploy' if re.search(r'\bdeploy(?:ment)?\b', text, re.I) else
            'ci' if probe[0] == 'ci' or re.search(r'\bCI\b|actions/runs', text, re.I) else 'external')
    url = re.search(r'https://github\.com/[^\s<>"\']+', text)
    ref = url.group(0).rstrip('.,;)') if url else (label if not label.startswith('probe ') else str(payload.get('reason') or label))
    return {'kind':kind, 'ref':ref or probe[1]}, wait


def healthy_future_park(card: Card, park: dict, now: float) -> bool:
    """Observe core's probe receipts, never run a command or confuse merge with deployment.

    A future backstop alone is insufficient. Receipts must belong to this park,
    match its command where recorded, and still be fresh. New failures supersede
    old health. Core rate-limits remote alive receipts to three hours.
    """
    payload = park['payload']
    if card.status != 'scheduled' or (_num(payload.get('wake_at')) or 0) <= now:
        return False
    command = str(payload.get('wake_check') or '').strip()
    events = _events(card.conn, card.id, ('external_watch', 'wake_check_alive',
                     'wake_check_broken', 'wake_check_lost', 'wake_check_stale', 'condition_met'))
    if not events:
        return False
    event = events[0]
    if event['id'] <= park['id'] or not park['at'] <= event['at'] <= now:
        return False
    data = event['payload']
    if not command:
        # Native GitHub watchers have a typed producer identity, not a shell
        # wake_check. Require the exact CI URL in this park and its fresh receipt.
        awaited = payload.get('awaiting') or {}
        if not (isinstance(awaited, dict) and awaited.get('kind') == 'ci'
                and event['kind'] == 'external_watch' and data.get('adapter') == 'github_run'
                and data.get('url') == awaited.get('ref')
                and re.fullmatch(r'https://github\.com/[^/]+/[^/]+/actions/runs/\d+', str(data.get('url') or ''))):
            return False
    recorded = str(data.get('wake_check') or (data.get('_wake_episode_v1') or {}).get('cmd') or '').strip()
    if recorded and recorded != command:
        return False
    if event['kind'] == 'wake_check_alive':
        if recorded != command:
            return False
        cadence = min(21600, max(10800, _num(payload.get('check_every')) or 900))
        return now - event['at'] <= cadence + 900
    if event['kind'] == 'external_watch':
        observed = _num(data.get('observed_at'))
        due = _num(data.get('next_due_at'))
        every = _num(data.get('every'))
        healthy_state = (data.get('state') == 'external_running' or
                         (data.get('adapter') == 'shell' and data.get('state') == 'external_unknown'))
        # A successful shell poll returning 'not yet' is healthy observation,
        # not proof that its custom condition happened or that the owner is stuck.
        return (data.get('health') == 'ok' and healthy_state
                and data.get('consecutive_failures') == 0 and observed is not None
                and every is not None and 0 < every <= 21600 and due is not None
                and park['at'] <= observed <= now and due <= observed + every + 900
                and now <= due + 900)
    return False


def healthy_widened_ci_wait(card: Card, park: dict, live: dict, now: float) -> bool:
    """A completed component is not completion of core's same-head aggregate watch."""
    payload = park['payload']
    awaited = payload.get('awaiting') or {}
    state = live.get('state')
    if (card.status != 'scheduled' or (_num(payload.get('wake_at')) or 0) <= now
            or payload.get('wake_check') or not isinstance(awaited, dict)
            or awaited.get('kind') != 'ci' or not isinstance(state, dict)
            or state.get('status') != 'completed' or state.get('conclusion') != 'success'):
        return False
    ref = awaited.get('ref')
    match = re.fullmatch(r'https://github\.com/([^/]+/[^/]+)/actions/runs/(\d+)', str(ref))
    sha = state.get('headSha')
    if (not match or state.get('url') != ref or not isinstance(sha, str)
            or not re.fullmatch(r'[0-9a-f]{40}', sha)):
        return False
    # The caller's legacy park selector uses arrival IDs. Do not let an older
    # remapped park lend its widening to a newer, unwidened episode.
    kinds = tuple(_core_wait()[0])
    newest = card.conn.execute(
        f"SELECT id,created_at FROM task_events WHERE task_id=? AND kind IN ({','.join('?' for _ in kinds)}) "
        "ORDER BY created_at DESC LIMIT 2", (card.id, *kinds)).fetchall()
    if (not newest or newest[0][0] != park['id'] or type(newest[0][1]) is not int
            or not 0 <= newest[0][1] <= now
            or len(newest) == 2 and newest[0][1] == newest[1][1]):
        return False
    rows = card.conn.execute("""SELECT id, kind, payload, created_at, run_id FROM task_events
        WHERE task_id=? AND created_at>? AND created_at<=? AND kind IN
        ('ci_wait_widened','external_watch','wake_check_broken','wake_check_lost',
         'wake_check_stale','condition_met') ORDER BY created_at DESC LIMIT 101""",
        (card.id, park['at'], now)).fetchall()
    if len(rows) == 101:
        return False  # incomplete history cannot establish absence of an adverse event
    events = [{'id': r[0], 'kind': r[1], 'payload': _json(r[2]) or {}, 'at': r[3]}
              for r in rows]
    # Compare immutable time, not replica-local arrival IDs. Ambiguous chronology
    # or any adverse event remains eligible; no source receipt is created here.
    if not all(isinstance(e['payload'], dict) for e in events):
        return False
    watches = [e for e in events if e['kind'] == 'external_watch']
    widened = [e for e in events if e['kind'] == 'ci_wait_widened']
    if not watches or not widened:
        return False
    watch = max(watches, key=lambda e: e['at'])
    widen = max(widened, key=lambda e: e['at'])
    if (sum(e['at'] == watch['at'] for e in watches) != 1
            or sum(e['at'] == widen['at'] for e in widened) != 1
            or not widen['at'] < watch['at']
            or any(e['kind'] not in ('external_watch', 'ci_wait_widened') for e in events)):
        return False
    data, bound = watch['payload'], widen['payload']
    pending_runs = bound.get('pending_runs')
    since = _num(bound.get('since'))
    if (type(bound.get('from_run')) is not int or bound['from_run'] != int(match[2])
            or bound.get('head_sha') != sha or not isinstance(pending_runs, list)
            or not pending_runs or not all(type(n) is int and n > 0 for n in pending_runs)
            or since is None or not park['at'] <= since <= widen['at']
            or data.get('adapter') != 'github_run'
            or data.get('url') != f'https://github.com/{match[1]}/commit/{sha}'):
        return False
    observed, due, every = (_num(data.get(k)) for k in ('observed_at', 'next_due_at', 'every'))
    return (data.get('health') == 'ok' and data.get('state') == 'external_running'
            and data.get('consecutive_failures') == 0 and observed is not None
            and every is not None and 0 < every <= 21600 and due is not None
            and widen['at'] <= observed <= watch['at'] <= now
            and observed <= due <= observed + every + 900 and now <= due + 900)


def reassessment_target(awaited: dict, live: dict, now: Optional[float] = None) -> Optional[dict]:
    """Derive identity/revision from an independent producer read, never receipt JSON.

    Source-host queue/update observers are opt-in administrator-configured adapters.
    They prove the selected target is still pending, not general producer health.
    """
    ref = awaited.get('ref')
    observation = live.get('target_observation')
    if observation is not None:
        spec = importlib.util.spec_from_file_location('_session_repair_targets', Path(__file__).with_name('target_reader.py'))
        targets = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(targets)
        if (now is not None and isinstance(ref,str) and targets.SELECTOR.fullmatch(ref)
                and not live.get('happened') and live.get('state') == 'owner_target_pending'
                and not awaited.get('gateway') and not awaited.get('board')
                and targets.valid_observation(observation, ref, now)):
            return dict(observation['target'])
        return None
    state = live.get('state')
    if (live.get('happened') or not isinstance(state, dict)
            or state.get('url') != ref or awaited.get('gateway') or awaited.get('board')):
        return None
    if re.fullmatch(r'https://github\.com/[^/]+/[^/]+/actions/runs/\d+', str(ref)):
        if (state.get('status') not in ('queued', 'in_progress', 'waiting', 'pending', 'requested')
                or state.get('conclusion') not in (None, '')):
            return None
        revision = state.get('headSha')
    elif re.fullmatch(r'https://github\.com/[^/]+/[^/]+/pull/\d+', str(ref)):
        if state.get('state') != 'OPEN' or state.get('reviewDecision') == 'CHANGES_REQUESTED':
            return None
        checks = state.get('statusCheckRollup')
        if not isinstance(checks, list) or any(
                not isinstance(check, dict) or check.get('conclusion') not in (None, '', 'SUCCESS', 'NEUTRAL', 'SKIPPED')
                or check.get('state') in ('ERROR', 'FAILURE') for check in checks):
            return None
        revision = state.get('headRefOid')
    else:
        return None
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        return None
    return {'kind': 'external', 'identity': ref, 'revision': revision}


def canonical_age_reassessment(card: Card, src: Sources, park: dict, awaited: dict, live: dict) -> bool:
    """Read-only, exact-episode age exemption; unavailable proof always fails closed.

    Keep receipt, live task, adverse events and current park in one SQLite read
    snapshot. Do not create stores, fetch/renew receipts, or trust pending JSON.
    """
    target = reassessment_target(awaited, live, src.now)
    if target is None:
        return False
    c = card.conn
    own_snapshot = not c.in_transaction
    try:
        from hermes_cli.kanban_owner_reassessment import producer
        from hermes_cli.kanban_owner_reassessment_replica import verified_for_event
        if own_snapshot:
            c.execute('BEGIN')
        def row(sql, args):
            cur = c.execute(sql, args)
            value = cur.fetchone()
            return dict(zip((d[0] for d in cur.description), value)) if value else None
        task = row('SELECT * FROM tasks WHERE id=?', (card.id,))
        event = row('SELECT * FROM task_events WHERE id=? AND task_id=?', (park['id'], card.id))
        receipt = verified_for_event(c, event, task, now=int(src.now)) if event and task else None
        if not event or not task or not receipt or not src.now < receipt['reassess_at']:
            return False
        if (receipt['target'] != target
                or any((awaited.get(k) or '') != (receipt['producer'].get(k) or '')
                       for k in ('kind', 'ref', 'gateway'))
                or producer(park['payload'].get('awaiting')) != receipt['producer']):
            return False
        live_event = ' AND deleted_at IS NULL' if _col(c, 'task_events', 'deleted_at') else ''
        for ref in receipt['evidence_refs']:
            if not c.execute('SELECT 1 FROM task_events WHERE uid=? AND task_id=?' + live_event,
                             (ref['uid'], card.id)).fetchone():
                return False
        # Replica ids are arrival order, not origin chronology. Only portable
        # timestamps can prove an observation predates this exact certified park.
        # Same-second events remain ambiguous: never use local ids or backfilled
        # UUIDv7 clocks to turn uncertain adverse evidence into an exemption.
        adverse_kinds = ('wake_check_broken', 'wake_check_lost', 'wake_check_stale', 'condition_met')
        cur = c.execute("SELECT kind,created_at,payload FROM task_events WHERE task_id=? AND kind IN "
                        "('external_watch','wake_check_broken','wake_check_lost','wake_check_stale','condition_met')"
                        + live_event, (card.id,))
        observations = [dict(zip((d[0] for d in cur.description), value)) for value in cur.fetchall()]
        watches = []
        park_at = event['created_at']
        if type(park_at) is not int or not 0 <= park_at <= src.now:
            return False
        for observation in observations:
            at = observation['created_at']
            if type(at) is not int or not 0 <= at <= src.now:
                return False
            if at < park_at:
                continue
            if observation['kind'] in adverse_kinds:
                return False
            watches.append(observation)
        # A healthy receipt must be unambiguously later than every conflicting
        # watcher. Equal timestamps cannot be resolved by replica arrival order.
        latest_at = max((value['created_at'] for value in watches), default=None)
        for watch in watches:
            if watch['created_at'] != latest_at:
                continue
            data = _json(watch['payload']) or {}
            if (not isinstance(data, dict) or data.get('health') != 'ok'
                    or data.get('consecutive_failures') != 0 or data.get('state') != 'external_running'):
                return False
        if task.get('lease_holder') and (_num(task.get('lease_expires')) or 0) <= src.now * 1000:
            return False
        if c.execute("SELECT 1 FROM task_runs WHERE task_id=? AND (status='running' OR started_at>?) LIMIT 1",
                     (card.id, park['at'])).fetchone():
            return False
        if _promotion_held(c, card):
            return False
        from hermes_cli.kanban_spend_decision import pending, execution_refusal
        return not (pending(c, card.id) or execution_refusal(c, card.id))
    except ModuleNotFoundError as exc:
        # An absent optional core capability is silent non-authority. Any other
        # missing module (a broken dependency of a capable core) is a real error.
        if not (exc.name or '').startswith('hermes_cli.kanban_owner_reassessment'):
            src.error(f'owner reassessment {card.board}/{card.id}', exc)
        return False
    except Exception as exc:  # unreadable evidence on a capable core cannot authorize
        src.error(f'owner reassessment {card.board}/{card.id}', exc)
        return False
    finally:
        if own_snapshot and c.in_transaction:
            c.rollback()  # ends only our read transaction; never ends a borrowed caller's transaction


def healthy_producer_card(ref: str, card: Card, live: dict, src: 'Sources', now: float) -> bool:
    """The awaited card is actively executing, or parked by core with a future timed wake."""
    state = live.get('state')
    if not isinstance(state, dict):
        return False
    board, _ = src.find_card(ref, card.board)
    if not board:
        return False
    c = src.conn(board)
    if state.get('status') == 'running':
        return c.execute("SELECT 1 FROM task_runs WHERE task_id=? AND status='running' AND ended_at IS NULL LIMIT 1",
                         (ref,)).fetchone() is not None
    if state.get('status') != 'scheduled':
        return False
    park = _events(c, ref, _core_wait()[0])
    if not park:
        return False
    started = c.execute('SELECT MAX(started_at) FROM task_runs WHERE task_id=?', (ref,)).fetchone()
    if started and started[0] is not None and park[0]['at'] < started[0]:
        return False  # that park is spent; a newer one has not arrived
    wake_at = _num(park[0]['payload'].get('wake_at'))
    return wake_at is not None and wake_at > now


def healthy_live_producer(card: Card, park: dict, awaited: dict, live: dict, src: 'Sources', now: float) -> bool:
    """True when the awaited producer is demonstrably alive, so a long wait is not a stall.

    card: the producer card is itself running a live run, or parked with a future timed wake
    (ready, blocked, triage, done and archived are not live producers). ci/deploy/external:
    core's watcher for this park observed the producer healthy (ok, or a GitHub run degraded while still running), with no failures, and its next poll
    is not overdue. Any adverse watcher event since the park (broken/lost/stale probe,
    condition_met) keeps the card eligible.
    """
    if awaited.get('kind') == 'card':
        return healthy_producer_card(awaited['ref'], card, live, src, now)
    if awaited.get('kind') not in ('ci', 'deploy', 'external'):
        return False
    payload = park['payload']
    if card.status != 'scheduled' or (_num(payload.get('wake_at')) or 0) <= now:
        return False  # only a future core backstop makes the watcher's word current
    command = str(payload.get('wake_check') or '').strip()
    ref = str(awaited.get('ref') or '')
    adverse = ('wake_check_broken', 'wake_check_lost', 'wake_check_stale', 'condition_met')
    rows = card.conn.execute(
        "SELECT id, kind, payload, created_at FROM task_events WHERE task_id=? AND id>? AND created_at>=? "
        f"AND created_at<=? AND kind IN ('external_watch', {','.join('?' for _ in adverse)}) "
        "ORDER BY created_at DESC, id DESC LIMIT 101",
        (card.id, park['id'], park['at'], now, *adverse)).fetchall()
    if not rows or len(rows) == 101:
        return False
    if any(r[1] in adverse for r in rows):
        return False
    if len(rows) > 1 and rows[0][3] == rows[1][3]:
        return False  # equal timestamps cannot be ordered
    data = _json(rows[0][2])
    if not isinstance(data, dict):
        return False
    recorded = str(data.get('wake_check') or (data.get('_wake_episode_v1') or {}).get('cmd') or '').strip()
    if recorded and recorded != command:
        return False
    if not command and not (
            data.get('adapter') in ('github_run', 'github_pr_checks')
            and data.get('url') == ref and re.fullmatch(r'https://github\.com/[^/]+/[^/]+/(?:actions/runs|pull)/\d+', ref)):
        return False  # a typed GitHub watcher must name this exact run/PR
    observed, due, every = (_num(data.get(k)) for k in ('observed_at', 'next_due_at', 'every'))
    health, wstate = data.get('health'), data.get('state')
    if not ((health == 'ok' and wstate in ('external_running', 'external_unknown'))
            or (health == 'degraded' and wstate == 'external_running' and data.get('adapter') == 'github_run')):
        # degraded is bounded only for a GitHub run (core wakes the card once it stops queueing);
        # a shell probe may report degraded/running indefinitely for a wedged deploy
        return False
    return (data.get('consecutive_failures') == 0 and not data.get('failing_since')
            and observed is not None and every is not None and 0 < every <= 21600
            and due is not None and observed <= now and observed <= due <= observed + every + 900
            and now <= due + 900)


def rule_long_wait(card: Card, src: Sources) -> Optional[Finding]:
    if card.status not in ('scheduled', 'blocked', 'todo') or _human_request(card.task)[1]:
        return None
    c = card.conn
    if c.execute("SELECT 1 FROM task_runs WHERE task_id=? AND status='running' LIMIT 1", (card.id,)).fetchone():
        return None
    from hermes_cli.kanban_spend_decision import pending, execution_refusal
    if _promotion_held(c, card) or pending(c, card.id) or execution_refusal(c, card.id):
        return None  # containment, spend and independent recovery holds are not execution authority
    park = current_park(card)
    if not park or healthy_future_park(card, park, src.now):
        return None
    awaited, wait = wait_producer(card, park, src.now)
    if not awaited or awaited['kind'] not in LONG_WAIT_S:
        return None
    kind, ref = awaited['kind'], awaited['ref']
    threshold = LONG_WAIT_S[kind]
    # A projected wait can predate this episode; never age through an admitted run/re-park.
    since = max(park['at'], _num(wait.get('waiting_since')) or park['at'])
    waited = int(src.now - since)
    if src.closeout_waking(card.board, card.id, wait.get('premise')):
        return None
    live = {'state': 'not_resolvable', 'happened': False}
    if kind == 'card':
        board, producer = src.find_card(ref, card.board)
        if not producer or not board or producer.get('source') != 'live':
            return None  # no invented idle age for an unreadable/remote producer
        at = _last_event_at(src.conn(board), ref)
        if at is None or src.now - at < PRODUCER_QUIET_S:
            return None
        live = {'state': producer, 'last_event_at': at, 'happened': False}
    elif kind in ('ci', 'deploy', 'external'):
        live = src.producer_state(kind, ref)
    if healthy_widened_ci_wait(card, park, live, src.now):
        return None
    if not live.get('happened') and healthy_live_producer(card, park, awaited, live, src, src.now):
        return None  # a live, healthy producer is the explanation; age alone is not a stall
    if live.get('happened') and park['payload'].get('wake_check') and re.fullmatch(
            r'https://github\.com/[^/]+/[^/]+/pull/\d+(?:[/?#].*)?', str(ref)):
        # The explicit probe owns the condition (merge queue, installed ancestry,
        # serving boot, etc.). GitHub's green/closed PR is not its result. Keep
        # elapsed-time diagnosis if that probe becomes unmonitored or overdue.
        live = {**live, 'happened': False, 'condition_unproven': 'explicit wake_check'}
    if live.get('happened'):
        # Give core's 300s external watcher two full polls after completion.
        # updatedAt is conservative (later edits may delay us), unlike park age.
        completed = _num(live.get('happened_at'))
        if completed is None or src.now - completed < 600:
            return None
    if not live.get('happened') and waited < threshold:
        return None
    if not live.get('happened') and canonical_age_reassessment(card, src, park, awaited, live):
        return None  # only age; producer completion and every other rule retain their own verdict
    last = _events(c, card.id)
    return _finding('BS-long-wait', card, 'event_happened' if live.get('happened') else 'too_long',
                    f'potentially stuck: {kind} {ref}, waiting {_fmt_age(waited)}',
                    {'kind': kind, 'ref': ref, 'waited_s': waited, 'threshold_s': threshold,
                     'live_producer': live, 'park_id': park['id'], 'park_at': park['at'],
                     'last_event': last[0] if last else None},
                    {'kind': 'nudge', 'action': 'ask the owner whether it is stuck and continue', 'commands': [],
                     'why': 'long wait or producer event already happened; guarded owner reassessment wake'})


def quiet_chat_findings(src: Sources, only: Optional[str] = None) -> list[Finding]:
    out = []
    c = src.state_conn()
    if c is None:
        return out
    try:
        if only:
            # outcome-audit exposes this same stored outcome; revalidation reads only the exact target.
            stored = c.execute('SELECT outcome_json FROM sessions WHERE id=?', (only,)).fetchone()
            rows = [{'id':only, **(_json(stored[0]) or {})}] if stored else []
        else:
            rows = src.outcome_rows()
        linked = src.linked_chats()
        for row in rows:
            sid = str(row.get('id') or '')
            if sid in src.exclude_chats or sid in linked or row.get('kind') not in ('unresolved', 'waiting'):
                continue
            status = row.get('status') or {}
            if (isinstance(status, dict) and (status.get('coverage') or {}).get('state') == 'unknown'):
                continue  # a failed source read is not evidence of unfinished cardless work
            if sid.startswith('kanban_owner_') and row.get('reason') == 'no_summary':
                continue  # synthetic service custody, not a person's unanswered chat
            if (isinstance(status, dict) and status.get('kind') == 'done'
                    and (status.get('coverage') or {}).get('state') == 'complete'):
                continue  # canonical settled work overrides legacy no_summary/unresolved
            if row.get('items') or any(x.get('kind') in ('human', 'decision', 'ask', 'cron', 'card') for x in row.get('on') or []):
                continue
            source_filter = " AND source IN ('desktop','tui')"
            s = c.execute("SELECT title FROM sessions WHERE id=? AND archived=0 AND COALESCE(muted,0)=0 "
                          "AND COALESCE(end_reason,'') NOT IN ('compression') "
                          "AND COALESCE(end_reason,'') NOT LIKE 'moved_to_gateway:%'" + source_filter, (sid,)).fetchone()
            last = c.execute('SELECT id,timestamp FROM messages WHERE session_id=? AND active=1 ORDER BY id DESC LIMIT 1', (sid,)).fetchone()
            if not s or s[0] == REPAIR_CHAT_TITLE or not last:
                continue
            age = src.now-last[1]
            if not QUIET_CHAT_S < age <= NOTICE_LOOKBACK_S or src.chat_obligations(sid):
                continue
            out.append(Finding(rule='BS-quiet-chat', card=None, board=None, chat=sid, title=s[0] or '',
                               summary=f'potentially stuck: {row["kind"]} chat quiet {_fmt_age(age)}', variant='quiet',
                               evidence={'kind':'chat', 'ref':sid, 'waited_s':int(age), 'threshold_s':QUIET_CHAT_S,
                                         'last_message_id':last[0], 'last_activity':last[1], 'outcome':row,
                                         'live_producer':{'state':'no linked card, cron or live turn'}},
                               fix={'kind':'nudge','action':'ask the chat whether it is stuck and continue',
                                    'commands':[], 'why':'unresolved cardless chat has no executor'}, area='card'))
    except Exception as e:
        src.error('quiet chats', e)
    return out


CARD_RULES: tuple[Rule, ...] = (rule_long_wait, rule_ready_worker_free, rule_overdue_wake, rule_unexplained_wait, rule_parent_done,
                                rule_stuck_run, rule_review_no_reviewer, rule_broken_chain)


# ── BS-notice-undelivered (chat level) ─────────────────────────────────────────────────────────────────────────────
_NOTICE_SQL = """
SELECT s.id AS sid, s.title AS title, m.id AS msg_id, m.timestamp AS ts, m.content AS content,
       m.display_metadata AS meta
  FROM (SELECT session_id, MAX(id) AS nid FROM messages
         WHERE display_kind = 'kanban_notice' AND active = 1 AND role IN ('user', 'assistant', 'tool')
           AND timestamp > ?
         GROUP BY session_id) n
  JOIN sessions s ON s.id = n.session_id
  JOIN messages m ON m.id = n.nid
 WHERE s.archived = 0 AND COALESCE(s.muted, 0) = 0
   AND COALESCE(s.end_reason, '') <> 'compression' AND COALESCE(s.end_reason, '') NOT LIKE 'moved_to_gateway:%'
   AND m.id = (SELECT id FROM messages WHERE session_id = s.id AND active = 1
                 AND role IN ('user', 'assistant', 'tool') ORDER BY id DESC LIMIT 1)
"""


def _closeout_verdict(meta: dict, ts: float, now: float) -> Optional[tuple[str, str]]:
    """(variant, summary) when the notice's close-out ran out of the retry policy, else None (still in policy)."""
    closeout = meta.get('closeout')
    age = now - ts
    if not isinstance(closeout, dict):
        if age < NOTICE_STALE_S or meta.get('blocked_on_human'):
            return None  # in policy, or a notice core deliberately never wakes (a person's move)
        return 'never_claimed', f'notice {_fmt_age(age)} old, never claimed by the close-out sweeper'
    state = closeout.get('state')
    if state == 'error':
        return 'closeout_error', 'close-out wake failed for good (closeout=error)'
    if state == 'busy' and 'busy_attempts' in closeout:
        return 'closeout_busy', f'close-out found the chat busy {closeout.get("busy_attempts")} times and gave up'
    if state == 'deferred':
        due = _num(closeout.get('retry_at'))
        if due is not None and now - due > NOTICE_STALE_S:
            return 'deferred_overdue', f'deferred close-out retry {_fmt_age(now - due)} past due'
    if state == 'claimed':
        at = _num(closeout.get('at'))
        if at is not None and now - at > NOTICE_STALE_S:
            return 'claim_stuck', f'close-out claimed {_fmt_age(now - at)} ago and never stamped a verdict'
    return None


def notice_findings(src: Sources, on_person: Callable[[str], bool]) -> list[Finding]:
    """Chats whose last message is a Kanban notice the close-out guarantee gave up on (or never reached)."""
    out: list[Finding] = []
    c = src.state_conn()
    if c is None:
        return out
    now = src.now
    try:
        cols = {r[1] for r in c.execute('PRAGMA table_info(messages)')}
        if not {'display_kind', 'display_metadata', 'active'} <= cols:
            return out
        rows = c.execute(_NOTICE_SQL, (now - NOTICE_LOOKBACK_S,)).fetchall()
    except Exception as e:  # noqa: BLE001
        src.error('notices', e)
        return out
    for r in rows:
        if str(r['sid']) in src.exclude_chats:
            continue  # never re-deliver into the repair chat itself
        meta = _json(r['meta']) or {}
        ts = _num(r['ts'])
        if ts is None:
            continue
        verdict = _closeout_verdict(meta, ts, now)
        if verdict is None:
            continue
        cards = sorted(set(src.card_id.findall(str(r['content'] or ''))))
        if verdict[0] == 'never_claimed' and any(on_person(cid) for cid in cards):
            continue  # a card it names is blocked on a person: core refreshes, never wakes (by design)
        variant, summary = verdict
        out.append(Finding(rule='BS-notice-undelivered', card=None, board=None, chat=str(r['sid']),
                           title=str(r['title'] or ''), summary=summary, variant=variant, area='hermes',
                           evidence={'msg_id': r['msg_id'], 'notice_age_s': int(now - ts), 'closeout': meta.get('closeout'),
                                     'cards': cards, 'notice_head': str(r['content'] or '')[:200]},
                           fix={'kind': 'auto', 'action': 're-deliver: start one close-out turn in the chat',
                                'commands': [f'hermes chat --resume {r["sid"]} --oneshot -Q -q "[Kanban close-out] The '
                                             'Kanban notice above reached this chat and no turn followed it. Act on it now."'],
                                'why': 'the same turn core\'s notice guarantee starts; it gave up or never ran'}))
    return out


def subscription_findings(src: Sources, chats_alive: Callable[[str], bool]) -> list[Finding]:
    """`tui` subscriptions whose oldest undelivered notify event is > 60 min old (the sweeper never wrote the row)."""
    out: list[Finding] = []
    now = src.now
    try:
        from tui_gateway.session_notifications import _KANBAN_NOTIFY_KINDS as kinds
    except Exception:  # noqa: BLE001
        kinds = NOTIFY_KINDS_FALLBACK
    kinds = tuple(kinds)
    for slug, _p in src.boards():
        c = src.conn(slug)
        if c is None:
            continue
        try:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='kanban_notify_subs'").fetchone():
                continue
            rows = c.execute(
                "SELECT s.task_id, s.chat_id, s.last_event_id, s.inflight_from, "
                f"(SELECT MIN(e.id) FROM task_events e WHERE e.task_id = s.task_id AND e.id > s.last_event_id "
                f" AND e.kind IN ({','.join('?' * len(kinds))})) AS eid "
                "FROM kanban_notify_subs s WHERE s.platform = 'tui'", kinds).fetchall()
            for r in rows:
                if r['eid'] is None or r['inflight_from'] is not None:
                    continue
                e = c.execute('SELECT kind, created_at FROM task_events WHERE id = ?', (r['eid'],)).fetchone()
                lag = _age(now, _num(e[1])) if e else None
                if (lag is None or lag < SUB_LAG_S or lag > NOTICE_LOOKBACK_S or str(r['chat_id']) in src.exclude_chats
                        or not chats_alive(str(r['chat_id']))):
                    continue
                out.append(Finding(rule='BS-notice-undelivered', card=str(r['task_id']), board=slug, chat=str(r['chat_id']),
                                   title='', summary=f'{e[0]} event on {r["task_id"]} undelivered to its chat for {_fmt_age(lag)}',
                                   variant='subscription_lag', area='hermes',
                                   evidence={'event_id': r['eid'], 'event_kind': e[0], 'lag_s': lag,
                                             'cursor': r['last_event_id']},
                                   fix={'kind': 'card', 'action': 'find why the unloaded sweeper skips this subscription '
                                        '(gateway log: "kanban-unloaded", "poller miss") and fix it',
                                        'commands': [kanban_cmd(slug, 'notify-list', r['task_id'])],
                                        'why': 'the sweeper retries every pass; a lag this long is a defect in it'}))
        except Exception as e:  # noqa: BLE001
            src.error(f'subscriptions {slug}', e)
    return out


# ── the scan ───────────────────────────────────────────────────────────────────────────────────────────────────────
def _alive_chats(src: Sources) -> Callable[[str], bool]:
    c = src.state_conn()
    cache: dict[str, bool] = {}

    def alive(sid: str) -> bool:
        if sid not in cache:
            try:
                r = c.execute('SELECT archived FROM sessions WHERE id = ?', (sid,)).fetchone() if c is not None else None
                cache[sid] = bool(r) and not r[0]
            except Exception:  # noqa: BLE001
                cache[sid] = False
        return cache[sid]
    return alive


def _allot_free_workers(findings: dict) -> None:
    """Many ready cards can see the same free worker slots. Keep a `free_worker` finding only for the cards those
    slots would have taken (oldest wait first, one slot each): with 3 free slots and 15 ready cards, the 3 longest
    waiters are the anomaly, the other 12 a queue."""
    cands = sorted((f for f in findings.values() if f.rule == 'BS-ready-worker-free' and f.variant == 'free_worker'),
                   key=lambda f: -(f.evidence.get('ready_for_s') or 0))
    left: dict[str, int] = {}
    for f in cands:
        machines = f.evidence.get('free_machines') or {}
        for name, slots in machines.items():
            left.setdefault(name, int(slots))
        pick = next((n for n in sorted(machines, key=lambda n: -left.get(n, 0)) if left.get(n, 0) > 0), None)
        if pick is None:
            findings.pop(f.fingerprint, None)
        else:
            left[pick] -= 1
            f.evidence['slot'] = pick


def scan(src: Optional[Sources] = None, *, rules: Optional[Iterable[str]] = None) -> dict:
    """Every finding for this gateway: `{scanned_at, chats, cards, findings: [...], errors: [...]}`."""
    src = src or Sources()
    wanted = set(rules or RULES)
    started = time.monotonic()
    findings: dict[str, Finding] = {}
    linked = src.linked_chats()
    chats = {k: v for k, v in linked.items() if k not in src.exclude_chats}
    truncated = max(0, len(chats) - MAX_CHATS)
    if truncated:
        log.warning('session-repair: %d chats over MAX_CHATS=%d not scanned', truncated, MAX_CHATS)
        src.errors.append({'where': 'scan', 'error': f'{truncated} chats over MAX_CHATS={MAX_CHATS} not scanned'})
    judged: set[tuple[str, str]] = set()
    human_blocked: dict[str, bool] = {}

    def judge(board: str, cid: str, chat: str, row: Optional[dict], via: Optional[str]) -> None:
        if (board, cid) in judged:
            return
        judged.add((board, cid))
        task, conn = src.task(board, cid)
        if task is None or conn is None:
            return
        card = Card(id=cid, board=board, chat=chat, task=task, conn=conn, row=row, via=via)
        human_blocked[cid] = card.status in ('blocked', 'triage') and _human_request(task)[1]
        if card.status in TERMINAL or card.ours or cid in src.ours_ids() or cid in src.copies(board):
            return  # finished; filed by the repair session itself; or a worker copy its home board owns
        for rule in CARD_RULES:
            rid = 'BS-' + rule.__name__[len('rule_'):].replace('_', '-')
            if rid not in wanted:
                continue
            try:
                f = rule(card, src)
            except Exception as e:  # noqa: BLE001 -- one rule on one card never stops the scan
                src.error(f'{rid} {board}/{cid}', e)
                continue
            if f is not None and f.fingerprint not in findings:
                findings[f.fingerprint] = f

    for chat in sorted(chats)[:MAX_CHATS]:
        bundle = src.bundle(chat) or {}
        rows = {str(t.get('id')): t for t in bundle.get('tasks') or [] if isinstance(t, dict)}
        for board, cid in sorted(chats[chat]):
            judge(board, cid, chat, rows.get(cid), None)
        live = {b for b, _ in src.boards()}
        for cid, rec in (bundle.get('cards') or {}).items():  # the chain: cards the linked ones wait on
            board = rec.get('board') or 'default'
            if (rec.get('detail') or {}).get('source') != 'live' or board not in live:
                continue  # a replica (another gateway's card): its owner judges it
            judge(board, cid, chat, rows.get(cid), None if cid in rows else 'chain')

    if 'BS-long-wait' in wanted:
        for board, _path in src.boards():
            c = src.conn(board)
            if c is not None:
                for r in c.execute("SELECT id FROM tasks WHERE status IN ('scheduled','blocked','todo')"):
                    if (board, str(r[0])) not in judged:
                        # Only the new rule widens the historic linked-card scan.
                        task, conn = src.task(board, str(r[0]))
                        if task and str(r[0]) not in src.copies(board) and str(r[0]) not in src.ours_ids():
                            card = Card(str(r[0]), board, None, task, conn, None)
                            if not card.ours and getattr(task, 'session_id', None) not in src.exclude_chats:
                                try:
                                    f = rule_long_wait(card, src)
                                    if f:
                                        findings.setdefault(f.fingerprint, f)
                                except Exception as e:
                                    src.error(f'BS-long-wait {board}/{r[0]}', e)
    def on_person(cid: str) -> bool:
        """Whether a card a notice names is blocked on a valid ask (core never wakes such a notice)."""
        if cid not in human_blocked:
            human_blocked[cid] = False
            for board, _p in src.boards():
                task, _c = src.task(board, cid)
                if task is not None:
                    human_blocked[cid] = task.status in ('blocked', 'triage') and _human_request(task)[1]
                    break
        return human_blocked[cid]

    alive = _alive_chats(src)
    if 'BS-notice-undelivered' in wanted:
        for f in notice_findings(src, on_person) + subscription_findings(src, alive):
            findings.setdefault(f.fingerprint, f)
    if 'BS-quiet-chat' in wanted:
        for f in quiet_chat_findings(src):
            findings.setdefault(f.fingerprint, f)
    _allot_free_workers(findings)
    for f in findings.values():  # the turn comments on an open fix card instead of filing a second one
        if f.fix.get('kind') == 'card':
            open_card = src.open_fix_card(f.fingerprint)
            if open_card:
                f.evidence['open_fix_card'] = open_card
                f.fix = {**f.fix, 'open_fix_card': open_card['id']}
    out = sorted(findings.values(), key=lambda f: (RULES.index(f.rule), f.board or '', f.card or '', f.chat or ''))
    return {'scanned_at': src.now, 'duration_s': round(time.monotonic() - started, 3), 'chats': len(chats),
            'cards': len(judged), 'truncated_chats': truncated, 'excluded_chats': sorted(src.exclude_chats),
            'findings': [f.to_dict() for f in out], 'errors': src.errors[:50],
            'error_count': len(src.errors)}


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description='Read-only scan for broken sessions (prints findings JSON).')
    p.add_argument('--rule', action='append', choices=RULES)
    args = p.parse_args(argv)
    print(json.dumps(scan(rules=args.rule), indent=2, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
