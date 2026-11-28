"""Server-owned Ledger view model. Desktop renders numbers, never counts rows."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
