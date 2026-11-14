"""Advisory fairness only: timestamps never authorize a repository operation."""
import hashlib
import json
import math

REVISIT = 6 * 3600


class MaintenanceSchedule:
    def __init__(self, config, runtime, state, now):
        self.state, self.now = state, now
        self.owner = {k: config[k] for k in ('owner_gateway_id', 'owner_profile', 'owner_epoch')}
        self.credentials = runtime.get('credentials', {})
        self.keys = set()

    def key(self, kind, item):
        ref = item['credential_ref']
        stable = {k: item[k] for k in ('id', 'kind', 'endpoint', 'name', 'credential_ref') if k in item}
        identity = [self.owner, kind, stable, self.credentials.get(ref)]
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        self.keys.add(key)
        return key

    def last(self, key):
        row = self.state.get(key)
        value = row.get('at') if type(row) is dict else None
        return min(value, self.now) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else -REVISIT

    def due(self, key, *, priority=False):
        return priority or self.now - self.last(key) >= REVISIT

    def attempted(self, key):
        self.state[key] = {'at': self.now}

    def prune(self):
        for key in set(self.state) - self.keys:
            del self.state[key]
