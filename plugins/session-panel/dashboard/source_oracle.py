"""Offline semantic expectations and unchanged-model custody (UNACTIVATED).

Expectations are supplied independently by an observation owner or canonical
pinned-input control, never inferred from raw task status or the emitted model.
This module compares presentation; it does not classify or authorize anything.
"""
from __future__ import annotations

import base64
import json
from bisect import bisect_right

SCHEMA = 'panel-semantic-expectations/1'
SECTIONS = ('needs_you', 'stuck', 'running', 'waiting', 'ready', 'done')
OWNER = ('id', 'key', 'gateway', 'profile', 'board')
MAX_CASES = 5000
MAX_ROWS = 4096


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def endpoint(window, admission):
    return canon([window, admission['id'], admission['chat']])


def empty_map(value):
    return value == {} or value == {'entries': []}


class Oracle:
    """One independently specified expectation per exact admitted endpoint.

    rows[].owner is the full wire row identity, section null asserts absence from
    ALL sections and off-list groups. Present rows require exact title, status
    fields and complete actions (including exact action targets/bindings).
    """
    def __init__(self, manifest):
        self.enabled = manifest is not None
        self.errors = []
        self.cases = {}
        self.checked = self.passed = self.missing = 0
        self.used = set()
        self.satisfied = set()
        self.regressions = 0
        if manifest is None:
            return
        try:
            if (not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA
                    or not isinstance(manifest.get('cases'), list)
                    or not 0 < len(manifest['cases']) <= MAX_CASES):
                raise ValueError('expectation manifest schema/cases')
            for case in manifest['cases']:
                key = endpoint(case['window_id'], case['admission'])
                if key in self.cases:
                    raise ValueError('duplicate expectation endpoint')
                rows = case['rows']
                if not isinstance(rows, list) or not 0 < len(rows) <= MAX_ROWS:
                    raise ValueError('expectation rows')
                seen = set()
                for row in rows:
                    owner = row['owner']
                    if not isinstance(owner, dict) or set(owner) != set(OWNER):
                        raise ValueError('expectation owner')
                    if any(not isinstance(owner[k], str) for k in OWNER):
                        raise ValueError('expectation owner strings')
                    if owner['key'] != ':'.join(owner[k] for k in ('gateway', 'profile', 'board', 'id')):
                        raise ValueError('expectation owner key is not canonical')
                    identity = canon(owner)
                    if identity in seen:
                        raise ValueError('duplicate expected row')
                    seen.add(identity)
                    if row['section'] is not None:
                        if (row['section'] not in SECTIONS or not isinstance(row['title'], str)
                                or not isinstance(row['status'], dict) or not row['status']
                                or not isinstance(row['actions'], list)):
                            raise ValueError('expected row section/title/status/actions')
                        if not set(row['status']) <= {'detail', 'evidence_state', 'decision', 'wait_kind', 'queued'}:
                            raise ValueError('unsupported presentation status field')
                canon(case)
                self.cases[key] = case
        except (KeyError, TypeError, ValueError, RecursionError):
            self.errors.append('malformed semantic expectation manifest')
            self.cases.clear()

    def check(self, window, publication, body):
        result = self._check(window, publication, body)
        try:
            if result is False and endpoint(window, publication['admission']) in self.satisfied:
                self.regressions += 1
        except (KeyError, TypeError, ValueError):
            pass
        return result

    def _check(self, window, publication, body):
        self.checked += 1
        if not self.enabled:
            self.missing += 1
            return None
        try:
            key = endpoint(window, publication['admission'])
            case = self.cases.get(key)
            if case is None:
                self.missing += 1
                return False
            self.used.add(key)
            # Bind stable age/watch/lineage, not only an admission id.
            for field in ('watch_token', 'lineage_generation', 'first_admitted_mono_ns', 'first_admitted_wall_ns'):
                if canon(case['admission'][field]) != canon(publication['admission'][field]):
                    return False
            model = body['model']
            sections = model['sections']
            if not isinstance(sections, dict) or set(sections) != set(SECTIONS):
                return False
            located = []
            for section in SECTIONS:
                rows = sections[section]
                if not isinstance(rows, list):
                    return False
                located.extend((section, row) for row in rows)
            # Off-list shared-blocker groups also defeat an absence assertion.
            stack = [model.get('groups', [])]
            visited = 0
            while stack:
                item = stack.pop()
                visited += 1
                if visited > MAX_ROWS * 32:
                    return False
                if isinstance(item, dict):
                    if all(k in item for k in OWNER):
                        located.append(('group', item))
                    else:
                        stack.extend(item.values())
                elif isinstance(item, list):
                    stack.extend(item)
            for expected in case['rows']:
                owner = expected['owner']
                # Same key with contradictory owner is not an absent success.
                collisions = [(section, row) for section, row in located
                              if isinstance(row, dict) and (row.get('key') == owner['key']
                                  or all(row.get(k) == owner[k] for k in ('id', 'gateway', 'profile', 'board')))]
                hits = [(section, row) for section, row in collisions
                        if canon({k: row.get(k) for k in OWNER}) == canon(owner)]
                if expected['section'] is None:
                    if collisions or not empty_map(publication['missing_scopes']):
                        return False
                    scopes = publication['covered_scopes'].get('entries', [])
                    def covers(source, demand):
                        same_owner = all(source.get(k) == owner[k] for k in ('gateway', 'profile', 'board'))
                        explicit = any(isinstance(ref, dict) and ref.get('card_id') == owner['id']
                            and all(ref.get(k) == owner[k] for k in ('gateway', 'profile', 'board'))
                            for ref in demand.get('refs', []))
                        return same_owner or explicit
                    if not any(covers(source, demand) for source, demand in scopes):
                        return False
                    continue
                if len(hits) != 1 or len(collisions) != 1:
                    return False
                section, row = hits[0]
                if (section != expected['section'] or row.get('title') != expected['title']
                        or canon(row.get('actions')) != canon(expected['actions'])):
                    return False
                if any(k not in row or canon(row[k]) != canon(v) for k, v in expected['status'].items()):
                    return False
            self.passed += 1
            self.satisfied.add(key)
            return True
        except (KeyError, TypeError, ValueError, RecursionError):
            return False

    def report(self):
        verdict = ('NOT_EVALUATED' if not self.enabled else 'FAIL' if self.errors
                   or self.missing or self.regressions or len(self.satisfied) != len(self.cases)
                   else 'PASS' if self.checked else 'NO_PROOF')
        return dict(verdict=verdict, checked=self.checked, passed=self.passed,
                    satisfied_endpoints=len(self.satisfied), expected_endpoints=len(self.cases),
                    missing=self.missing, unused_cases=len(self.cases) - len(self.used), errors=self.errors)


def audit_no_change(records, successful, stored):
    """A no_change reference is separate evidence, never a new inclusion sample.

    successful is the analyzer's real validated inclusion list; stored maps exact
    publication keys to model_stored records. Missing/failed/invalidation-only
    predecessors cannot attest equivalence. No synthetic success is manufactured.
    """
    prior = {}
    deliveries = {}
    for item in successful:
        key = (item['window'], item['key'])
        prior[key] = min(prior.get(key, item['time']), item['time'])
        owner = (item['window'], item['key'][0], item['key'][2])
        deliveries.setdefault(owner, {}).setdefault(item['time'], set()).add(item['key'])
    delivery_index = {owner: (sorted(times), times) for owner, times in deliveries.items()}
    current_models = {}
    for row in records:
        if row['event'] == 'model_stored':
            pub = row.get('publication', {})
            attempt = pub.get('projection_attempt_id')
            if isinstance(attempt, str):
                current_models.setdefault((row['window_id'], attempt), []).append(row)
    checked = matched = unestablished = 0
    errors = []
    for row in records:
        if row['event'] != 'no_change':
            continue
        checked += 1
        publication = row.get('prior_successful_publication')
        if publication is None:
            unestablished += 1
            continue
        try:
            key = tuple(publication[k] for k in ('feed_epoch', 'publication_id', 'session', 'model_revision'))
            success_at = prior[(row['window_id'], key)]
            originals = stored[key]
            original = next(r for r in originals if r['window_id'] == row['window_id']
                            and canon(r['publication']) == canon(publication))
            if success_at > row['monotonic_ns']:
                raise ValueError('predecessor is not prior')
            times, keys = delivery_index[(row['window_id'], key[0], key[2])]
            pos = bisect_right(times, row['monotonic_ns']) - 1
            if pos < 0 or keys[times[pos]] != {key}:
                raise ValueError('predecessor is not latest successful delivery')
            current = row['admission']
            old = publication['admission']
            for field in ('chat', 'watch_token', 'lineage_generation'):
                if canon(current[field]) != canon(old[field]):
                    raise ValueError('predecessor owner/watch/lineage mismatch')
            if canon(row['chat']) != canon(current['chat']):
                raise ValueError('no_change chat mismatch')
            if row.get('feed_epoch', publication['feed_epoch']) != publication['feed_epoch']:
                raise ValueError('no_change feed epoch mismatch')
            model = json.loads(base64.b64decode(original['payload_b64'], validate=True))['model']
            model = {k: v for k, v in model.items() if k != 'freshness'}
            if canon(model) != canon(row['identical_model']):
                raise ValueError('no_change model differs')
            candidates = current_models[(row['window_id'], row['projection_attempt_id'])]
            current_record = next(r for r in reversed(candidates)
                                  if r['monotonic_ns'] <= row['monotonic_ns'])
            current_pub = current_record['publication']
            if canon(current_pub['admission']) != canon(current):
                raise ValueError('no_change current admission differs')
            if current_pub['feed_epoch'] != publication['feed_epoch']:
                raise ValueError('no_change current epoch differs')
            for field in ('dependencies', 'covered_scopes', 'missing_scopes'):
                if canon(row[field]) != canon(current_pub[field]):
                    raise ValueError('no_change current coverage differs')
            current_model = json.loads(base64.b64decode(current_record['payload_b64'], validate=True))['model']
            current_model = {k: v for k, v in current_model.items() if k != 'freshness'}
            if canon(model) != canon(current_model):
                raise ValueError('no_change prior/current serialized models differ')
            matched += 1
        except (KeyError, StopIteration, TypeError, ValueError, RecursionError):
            errors.append(row.get('record_id'))
    return dict(checked=checked, matched=matched, unestablished=unestablished, invalid=len(errors),
                invalid_sample=errors[:50])
