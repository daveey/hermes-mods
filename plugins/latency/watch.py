"""Chat proposals are inert; only an authenticated, explicit Add persists them.

The directive carries a bounded definition, not a path or remote command. Desktop
routes it to the same hub as the Ledger. This works from local or remote chats.
"""
import base64
from datetime import datetime
from zoneinfo import ZoneInfo
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

from .registry import load_yaml, registry

FIELDS = {'id', 'label', 'group', 'kind', 'source', 'start', 'end', 'unit', 'target_p90'}
GROUPS = {'Using Hermes', 'Cards & workers', 'Ship'}
EVENT = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}\Z')


def validate(definition):
    if not isinstance(definition, dict) or set(definition) != FIELDS:
        raise ValueError('definition requires exactly id, label, group, kind, source, start, end, unit, target_p90')
    d = dict(definition)
    for key in FIELDS-{'target_p90'}:
        if not isinstance(d[key], str) or not d[key] or len(d[key]) > 200 or any(ord(c)<32 for c in d[key]):
            raise ValueError('invalid '+key)
    if not EVENT.fullmatch(d['id']) or not EVENT.fullmatch(d['start']) or not EVENT.fullmatch(d['end']) or d['start']==d['end']:
        raise ValueError('invalid metric/event identifier')
    if d['group'] not in GROUPS or d['unit'] != 'ms' or d['kind'] not in ('derived','span'):
        raise ValueError('invalid group, kind or unit')
    target = d['target_p90']
    if isinstance(target,bool) or not isinstance(target,(int,float)) or not math.isfinite(target) or not 0 < target <= 90*86400000:
        raise ValueError('target_p90 must be positive finite milliseconds (at most 90 days)')
    if d['kind']=='derived':
        supported = d['source']=='kanban' and (d['start'],d['end']) in {('created','claimed'),('created','completed'),('decision_requested','unblocked')}
        supported |= d['source']=='state' and ((d['start']=='typed_user' and d['end'] in ('first_assistant','explicit_final')) or d['end']=='tool_result')
        if not supported:
            raise ValueError('unsupported derived projection; use a span definition for new instrumentation')
    elif d['source'] != 'spans':
        raise ValueError('span source must be spans')
    shipped = load_yaml(Path(__file__).with_name('metrics.yaml'))['metrics']
    if d['id'] in {x['id'] for x in shipped}:
        raise ValueError('watch cannot replace a shipped metric')
    return d


def propose(describe=None, definition=None):
    if (describe is None)==(definition is None):
        raise ValueError('provide describe OR definition')
    if describe is not None:
        if not isinstance(describe,str) or len(describe)>250:
            raise ValueError('describe must name start -> end (at most 250 characters)')
        pair = re.split(r'\s*(?:→|->)\s*',describe.strip())
        if len(pair)!=2 or not all(EVENT.fullmatch(s) for s in pair):
            raise ValueError('name exact start -> end events, or supply a complete definition')
        start,end=pair
        derived = (start,end) in {('decision_requested','unblocked'),('created','claimed'),('created','completed')}
        identity = hashlib.sha256((start+'\0'+end).encode()).hexdigest()[:16]
        definition = dict(id='watch.'+identity,label=start+' → '+end,group='Cards & workers',kind='derived' if derived else 'span',source='kanban' if derived else 'spans',start=start,end=end,unit='ms',target_p90=60000)
    d=validate(definition)
    encoded=base64.urlsafe_b64encode(json.dumps(d,ensure_ascii=False,separators=(',',':')).encode()).decode()
    return dict(definition=d,history='Backfill last 30 days within one collector cycle' if d['kind']=='derived' else 'Collecting from Add date; new instrumentation required',
                directive='::latency-watch{definition="'+encoded+'"}',
                instruction='Show directive on its own line. Add and Change target are user controls; this proposal has not been added. Target is a proposed p90 in ms.')


def handle(args, **kwargs):
    try:
        if not isinstance(args,dict) or set(args)-{'describe','definition'}:
            raise ValueError('provide describe OR definition')
        return json.dumps(propose(**args),ensure_ascii=False)
    except (ValueError,TypeError) as error:
        return json.dumps({'error':str(error)})


SCHEMA = {'name':'latency_watch','description':'When the user says an operation is slow, propose a latency metric with Add / Change target controls. Supply describe="start -> end" or a complete definition. Does not add anything until the user clicks Add. Emit the returned ::latency-watch directive on its own line. Exact known board pairs are derived and backfill 30 days; new events need a span instrumentation card. Targets are p90 milliseconds.',
          'parameters':{'type':'object','additionalProperties':False,'properties':{'describe':{'type':'string'},'definition':{'type':'object','properties':{k:{'type':'number' if k=='target_p90' else 'string'} for k in FIELDS},'required':sorted(FIELDS),'additionalProperties':False}}}}


def instrumentation_card(root, definition):
    """Use core's supported creation/publication API, with crash-safe dedupe."""
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    config_path=Path(root)/'latency/sources.json'
    config=json.loads(config_path.read_text()) if config_path.exists() else {}
    gateway=config.get('gateway')
    profile=config.get('profile','default')
    if not all(isinstance(v,str) and EVENT.fullmatch(v) and v not in ('unknown','local') for v in (gateway,profile)):
        raise ValueError('configure the collecting gateway/profile in latency/sources.json before adding spans')
    board=config.get('instrumentation_board')
    if board and not kb.board_exists(board):
        raise ValueError('instrumentation board does not exist')
    from hermes_cli.profiles import list_profiles
    assignee=config.get('instrumentation_assignee','default')
    # The configured profile must exist; never queue an unrouteable card.
    profiles=list_profiles(lazy_skill_count=True)
    names={p.name for p in profiles}
    if assignee not in names:
        raise ValueError('instrumentation assignee is not a configured profile')
    key='latency-instrumentation:'+definition['id']
    body=('Add instrumentation for the approved latency watch '+definition['id']+'.\n\n'
          'Definition: '+json.dumps(definition,ensure_ascii=False,sort_keys=True)+'\n\n'
          'Correlate '+definition['start']+' to '+definition['end']+' using stable card/request identity. '
          'Emit identifier-only latency spans for this metric; do not record prompts, bodies, queries or credentials. '
          'Use the existing latency span/OTLP contract. Test the correlation and the real collector/panel path. '
          'Definition is linked from ~/.hermes/latency/metrics.local.yaml on the collecting gateway. '
          'Do not fabricate history. Normal independent review, exact-head CI and landing gates apply.')
    with kbc.connect_closing(board=board) as conn:
        task=kb.create_task(conn,title='Instrument latency: '+definition['label'],body=body,assignee=assignee,
                            created_by='latency-watch',idempotency_key=key,board=board,
                            workspace_kind='scratch', review_required=True,
                            requires=['terminal','file','git','kanban','hub:read'])
        stored=kb.get_task(conn,task)
        if stored is None:
            raise RuntimeError('instrumentation creation not verified')
        if stored.body != body:
            raise ValueError('instrumentation id already exists with a different definition; retry the original proposal')
    return dict(id=task,board=board or kb.get_current_board(),gateway=gateway,profile=profile)


def add(root, definition, now=None, file_card=None):
    d=validate(definition)
    root=Path(root)
    now=time.time() if now is None else now
    folder=root/'latency'; folder.mkdir(parents=True,exist_ok=True)
    path=folder/'metrics.local.yaml'
    # One lock spans dedupe, instrumentation publication and atomic file replace.
    with (folder/'metrics.local.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        local=load_yaml(path) if path.exists() else {'metrics':[]}
        if not isinstance(local,dict) or set(local)!={'metrics'} or not isinstance(local['metrics'],list):
            raise ValueError('invalid local registry; refusing to overwrite it')
        registry(root) # validate existing content before modifying
        for old in local['metrics']:
            if old['id']==d['id']:
                if {k:old[k] for k in FIELDS} != d:
                    raise ValueError('metric id already exists with a different definition/target')
                return dict(added=False,definition=old)
        if len(local['metrics'])>=100:
            raise ValueError('local metric limit reached')
        entry={**d,'measured_since':now}
        if d['kind']=='span':
            ref=(file_card or (lambda row:instrumentation_card(root,row)))(d)
            if not isinstance(ref,dict) or set(ref)!={'id','board','gateway','profile'} or not all(isinstance(v,str) and EVENT.fullmatch(v) for v in ref.values()):
                raise ValueError('instrumentation card has no qualified reference')
            entry['instrumentation_card']=ref
        # JSON is YAML 1.2; preserves all existing definition values, avoids the
        # core YAML read cache, and publishes complete bytes with os.replace.
        payload=json.dumps({'metrics':[*local['metrics'],entry]},ensure_ascii=False,indent=2)+'\n'
        fd,name=tempfile.mkstemp(prefix='.metrics-',dir=folder)
        try:
            with os.fdopen(fd,'w') as stream:
                stream.write(payload); stream.flush(); os.fsync(stream.fileno())
            os.replace(name,path)
            directory=os.open(folder,os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.exists(name): os.unlink(name)
    return dict(added=True,definition=entry)


def collection_label(row, now=None):
    if row.get('n') or row.get('kind')!='span' or 'instrumentation_card' not in row:
        return None
    now=time.time() if now is None else now
    zone=ZoneInfo('America/Los_Angeles')
    today=datetime.fromtimestamp(now,zone).date()
    since=datetime.fromtimestamp(row['measured_since'],zone).date()
    return 'collecting from '+('today' if today==since else since.isoformat())
