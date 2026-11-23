"""Independent nudge lane: durable cadence, read-before-write, exact-target receipts.

No direct board SQL writes. Unknown delivery is retained, never blindly retried.
"""
import datetime
import json
import subprocess
import time
from pathlib import Path

INTERVAL_S = 2 * 3600
PER_RUN = 15
PER_DAY = 100
MAX_UNANSWERED = 3


def config_mode(home):
    try:
        value = json.loads((home / 'session-repair' / 'config.json').read_text()).get('nudge_mode')
    except (OSError, ValueError, AttributeError):
        value = None
    return value if value in ('shadow', 'live') else 'shadow'


def message(f, times):
    e = f['evidence']
    def age(n):
        n = int(n or 0)
        return f'{n//3600}h {n%3600//60}m'
    text = (f"[session-repair] You've been waiting on {e.get('kind')} {e.get('ref')} for "
            f"{age(e.get('waited_s'))} (limit {age(e.get('threshold_s'))}). Live state: "
            f"{json.dumps(e.get('live_producer'), default=str)}. Are you stuck? If the event happened, continue now. "
            "If the wait is still right, re-park with a fresh reason/wake. If you can't move, block with the real reason.")
    if times:
        stamps = ', '.join(datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat() for t in times)
        text += f' Nudge #{len(times)+1}; earlier nudges: {stamps}.'
    return text


def progress(scan, src, f):
    if f.get('card'):
        c = src.conn(f['board'])
        for ev in scan._events(c, f['card'], limit=200):
            if ev['kind'] == 'commented':
                row = c.execute('SELECT body FROM task_comments WHERE id=?', (ev['payload'].get('comment_id'),)).fetchone()
                if row and row[0].startswith('[session-repair]'):
                    continue
            return ev['id']
        return 0
    c = src.state_conn()
    row = c.execute("SELECT id FROM messages WHERE session_id=? AND active=1 "
                    "AND NOT (role='user' AND content LIKE '[session-repair]%') ORDER BY id DESC LIMIT 1", (f['chat'],)).fetchone()
    return row[0] if row else 0


def fresh(scan, f):
    # New reader scope, never the scan's transaction/cache. Re-run canonical exclusions.
    src = scan.Sources()
    if f.get('card'):
        if f['card'] in src.copies(f['board']) or f['card'] in src.ours_ids():
            return None, src
        task, c = src.task(f['board'], f['card'])
        if not task or getattr(task, 'session_id', None) in src.exclude_chats:
            return None, src
        card = scan.Card(f['card'], f['board'], f.get('chat'), task, c, None)
        current = None if card.ours else scan.rule_long_wait(card, src)
        current = current.to_dict() if current else None
        if current and current['evidence']['park_id'] != f['evidence']['park_id']:
            current = None
        if current and current['evidence']['last_event']['id'] != f['evidence']['last_event']['id']:
            current = None
    else:
        current = next((x.to_dict() for x in scan.quiet_chat_findings(src, only=f['chat']) if x.chat == f['chat']), None)
        if current and current['evidence']['last_message_id'] != f['evidence']['last_message_id']:
            current = None
    return current, src


def receipt(src, f, text):
    if f.get('card'):
        row = src.conn(f['board']).execute('SELECT id,body FROM task_comments WHERE task_id=? AND body=? ORDER BY id DESC LIMIT 1',
                                          (f['card'], text)).fetchone()
        return {'comment_id':row[0], 'board':f['board'], 'card':f['card']} if row else None
    row = src.state_conn().execute("SELECT id FROM messages WHERE session_id=? AND role='user' AND content=? AND active=1 ORDER BY id DESC LIMIT 1",
                                   (f['chat'],text)).fetchone()
    return {'message_id':row[0], 'chat':f['chat']} if row else None


def process(findings, *, home, now, scan, runner, argv, read_json, write_json, append):
    sd = home / 'session-repair'
    mode = config_mode(home)
    path = sd / f'nudges-{mode}.json'
    state = read_json(path, {'fps':{},'sent':[]})
    state['sent'] = [t for t in state.get('sent',[]) if t > now-86400]
    actions, escalations = [], []
    deadline = time.monotonic() + 300  # leave remaining candidates for the next 15-minute tick
    room = min(PER_RUN, max(0, PER_DAY-len(state['sent'])))
    for original in findings:
        if time.monotonic() >= deadline:
            break
        fp = original['fingerprint']
        history = state['fps'].setdefault(fp, {'times':[]})
        # Pending write identity is frozen across a crash/timeout. Read it back before deciding anything else.
        pending = history.get('pending')
        if pending:
            src = scan.Sources()
            rec = receipt(src, original, pending['text'])
            if rec:
                history['times'].append(pending['at'])
                history['receipt'] = rec
                history.pop('pending')
                append(sd/'nudges-live.jsonl', [{'at':now,'fingerprint':fp,'outcome':'nudged','receipt':rec,'recovered':True}])
            else:
                actions.append({'fingerprint':fp,'outcome':'delivery_unknown','pending_at':pending['at']})
                continue
        current, src = fresh(scan, original)
        if current is None:
            actions.append({'fingerprint':fp,'outcome':'moved_on'})
            continue
        checkpoint = progress(scan, src, current)
        if history.get('progress') != checkpoint:
            history.update(times=[], progress=checkpoint)
            history.pop('escalated', None)
        times = history['times']
        if times and now-times[-1] < INTERVAL_S:
            continue
        if len(times) >= MAX_UNANSWERED:
            if history.get('escalated'):
                continue
            current = {**current, 'fix':{'kind':'card','action':'investigate three unanswered session-repair nudges',
                                        'commands':[], 'why':'no owner event after three nudges'}, 'area':'card'}
            current['evidence']['nudge_times'] = list(times)
            current['evidence']['nudge_escalation'] = True
            existing = src.open_fix_card(fp)
            if existing:
                current['fix']['open_fix_card'] = existing['id']
                current['evidence']['open_fix_card'] = existing
            # The normal repair path owns dedupe, card caps, open-fix lookup and delivery.
            escalations.append(current)
            continue
        if room <= 0:
            continue
        text = message(current, times)
        marker = f"\n[session-repair receipt {fp} {now}]"
        text += marker
        action = {'at':now,'fingerprint':fp,'finding':current,'would':'nudge','nudge_count':len(times)+1,'earlier_times':list(times)}
        if mode == 'shadow':
            actions.append({**action,'outcome':'would_nudge'})
            times.append(now)
            state['sent'].append(now)
        else:
            history['pending'] = {'text':text,'at':now}
            state['sent'].append(now)  # reserve even uncertain delivery against the rolling cap
            write_json(path,state)  # write-ahead; a timeout cannot silently double-send
            if current.get('card'):
                cmd = argv + ['kanban','--board',current['board'],'comment',current['card'],text]
            else:
                cmd = argv + ['chat','--resume',current['chat'],'--oneshot','-Q','-q',text]
            try:
                if not current.get('card') and runner is subprocess.run:
                    # Wait for admission receipt, not turn completion. Never timeout-kill an owner's resumed work.
                    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, start_new_session=True)
                    history['pending']['pid'] = proc.pid
                    write_json(path, state)
                    rec = None
                    until = min(deadline, time.monotonic()+15)
                    while time.monotonic() < until:
                        rec = receipt(scan.Sources(), current, text)
                        if rec or proc.poll() is not None:
                            break
                        time.sleep(0.2)
                else:
                    runner(cmd, capture_output=True, text=True, timeout=max(1, min(30, deadline-time.monotonic())))
                    rec = receipt(scan.Sources(), current, text)
            except Exception:
                rec = None
            if rec:
                times.append(now)
                history.pop('pending',None)
                history['receipt'] = rec
                actions.append({**action,'outcome':'nudged','receipt':rec})
            else:
                actions.append({**action,'outcome':'delivery_unknown'})
        room -= 1
        write_json(path,state)
    write_json(path,state)
    append(sd/f'nudges-{mode}.jsonl',actions)
    if mode=='shadow':
        append(sd/'actions-shadow.jsonl',actions)
    return actions, escalations, mode
