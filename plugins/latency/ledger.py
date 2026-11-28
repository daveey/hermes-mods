"""Server-owned Ledger view model. Desktop renders numbers, never counts rows."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re


_SAFE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z')
_REPO = re.compile(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*\Z')


def sample_link(sample, definition, sources):
    """Resolve only explicit owner identifiers; never use the viewing gateway."""
    attrs = sample.get('attrs', {})
    def safe(value):
        return isinstance(value, str) and bool(_SAFE.fullmatch(value)) and value not in ('unknown', 'local')
    gateway, profile = sample.get('gateway'), attrs.get('profile')
    if safe(gateway) and safe(profile):
        if safe(attrs.get('session')):
            return dict(kind='session', id=attrs['session'], gateway=gateway, profile=profile)
        if safe(attrs.get('card')) and safe(attrs.get('board')):
            return dict(kind='card', id=attrs['card'], gateway=gateway, profile=profile, board=attrs['board'])
    source = definition.get('source', '')
    alias = source.removeprefix('github.')
    if source != 'github.' + alias or alias not in ('core', 'mods', 'client'):
        return None
    settings = sources.get('github', {}).get(alias, {})
    repo = settings.get('repo') if isinstance(settings, dict) else None
    if not isinstance(repo, str) or not _REPO.fullmatch(repo) or any(p in ('.', '..') for p in repo.split('/')):
        return None
    for attr, path, kind in (('pr', 'pull', 'pr'), ('run', 'actions/runs', 'run')):
        number = attrs.get(attr)
        if isinstance(number, str) and re.fullmatch(r'[1-9][0-9]*', number):
            return dict(kind=kind, url=f'https://github.com/{repo}/{path}/{number}')
    return None


def enrich(row, sources):
    slowest = []
    for sample in row.get('slowest', []):
        # Ignore any supplied link; only the server builds navigation targets.
        clean = {k: v for k, v in sample.items() if k != 'link'}
        link = sample_link(clean, row, sources)
        slowest.append({**clean, **({'link': link} if link else {})})
    return {**row, 'slowest': slowest}

GROUPS = ('Using Hermes', 'Cards & workers', 'Ship')


def decorate(row):
    value, target = row['p90'], row['target_p90']
    ratio = value/target if value is not None and target > 0 else None
    return {**row, 'ratio': ratio, 'status': 'none' if ratio is None else 'bad' if ratio > 2 else 'warn' if ratio > 1 else 'ok'}


def build(view, history, today):
    # Fill exactly eight local calendar dates, leaving unmeasured gaps as null.
    zone = ZoneInfo(view['timezone'])
    last = datetime.fromtimestamp(view['at'], zone).date()
    days = [(last-timedelta(days=7-i)).isoformat() for i in range(8)]
    histories = {r['id']: r for r in history['metrics']}
    def row(r):
        previous = {p['day']: p for p in histories[r['id']]['series']}
        return decorate({**r, 'spark': [previous.get(day, {'day': day, 'n': 0, 'p50': None, 'p90': None}) for day in days]})
    rows = [row(r) for r in view['metrics']]
    daily = [decorate(r) for r in today['metrics']]
    worst = sorted((r for r in daily if r['ratio'] is not None), key=lambda r: (-r['ratio'], r['id']))[:5]
    indexed = {r['id']: r for r in rows}
    return {**view, 'groups': [{'label': name, 'rows': [r for r in rows if r['group'] == name]} for name in GROUPS],
            'chip': {'over_target_count': sum(r['ratio'] is not None and r['ratio'] > 2 for r in daily),
                     'spark': indexed.get('turn_first', {}).get('spark', []),
                     'worst': [{**r, 'spark': indexed[r['id']]['spark']} for r in worst]}}
