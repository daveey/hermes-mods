"""Guarded GitHub reads and deployment joins; repository mapping is local config.

No persisted tokens, titles, bodies or review text. Day-sliced Actions queries
recursively split saturated days, and 5xx failures have bounded retries. A cursor
advances only after all queries and projections for that source succeed.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import quote

from .collectors import sample


def epoch(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


def api(path, query=None, run=subprocess.run, sleep=time.sleep):
    args = ['gh', 'api', path]
    if query is not None:
        args += ['-f', 'query=' + query]
    for attempt in range(4):
        result = run(args, capture_output=True, text=True, timeout=90)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if isinstance(data, dict) and data.get('errors'):
                raise RuntimeError('GitHub GraphQL returned errors')
            return data
        if attempt == 3 or not re.search(r'\b5\d\d\b', result.stderr):
            raise RuntimeError('GitHub read failed; cursor retained')
        sleep(2**attempt)


def runs(repo, since, until, fetch=api):
    """Inclusive second-resolution API bounds, deduped across adjacent slices."""
    found = {}

    def interval(a, b):
        base = f'repos/{repo}/actions/runs?per_page=100&created={quote(iso(a))}..{quote(iso(b))}'
        first = fetch(base + '&page=1')
        if first['total_count'] >= 1000:
            if b-a <= 1:
                raise RuntimeError('GitHub 1000-result cap in one second; cursor retained')
            middle = int((a+b)/2)
            interval(a, middle)
            interval(middle, b)
            return
        chunk = first['workflow_runs']
        for row in chunk:
            found[(row['id'], row.get('run_attempt', 1))] = row
        count = len(chunk)
        page = 2
        while len(chunk) == 100:
            chunk = fetch(base + f'&page={page}')['workflow_runs']
            for row in chunk:
                found[(row['id'], row.get('run_attempt', 1))] = row
            count += len(chunk)
            page += 1
        if count < first['total_count']:
            raise RuntimeError('Incomplete GitHub slice; cursor retained')
    start = int(since)
    while start < until:
        end = min(start+86400, int(until))
        if end <= start:
            break
        interval(start, end)
        start = end
    return list(found.values())


def pull_requests(repo, since, fetch=api):
    owner, name = repo.split('/')
    after = None
    found = []
    while True:
        cursor = ',after:' + json.dumps(after) if after else ''
        query = '{repository(owner:' + json.dumps(owner) + ',name:' + json.dumps(name) + '){pullRequests(first:100,states:MERGED,orderBy:{field:UPDATED_AT,direction:DESC}' + cursor + '){pageInfo{hasNextPage endCursor} nodes{number createdAt mergedAt updatedAt baseRefName mergeCommit{oid}}}}}'
        page = fetch('graphql', query=query)['data']['repository']['pullRequests']
        rows = page['nodes']
        found.extend(r for r in rows if epoch(r['mergedAt']) >= since)
        if not page['pageInfo']['hasNextPage'] or not rows or min(epoch(r['updatedAt']) for r in rows) < since:
            return found
        after = page['pageInfo']['endCursor']
        if len(found) > 20000:
            raise RuntimeError('PR scan bound exceeded; cursor retained')


def deployment_markers(root, checkout, kind, run=subprocess.run):
    """Commit positions on first-parent history: exact prefix resolution only."""
    def git(*args):
        result = run(['git', '-C', str(checkout), *args], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Deployment history unavailable')
        return result.stdout.splitlines()
    chain = {sha: i for i, sha in enumerate(git('log', '--first-parent', 'HEAD', '--format=%H'))}
    markers = []
    if kind == 'core':
        log = Path(root) / 'logs' / 'hermes-sync.log'
        if log.exists():
            for line in log.open(errors='replace'):
                if 'core: update exit 0:' not in line:
                    continue
                match = re.search(r'✓ default \(pid \d+\) @ ([0-9a-f]+)', line)
                if not match:
                    continue
                hits = [index for sha, index in chain.items() if sha.startswith(match[1])]
                if len(hits) == 1:
                    try:
                        markers.append((epoch(line[:19].replace(' ', 'T') + 'Z'), hits[0]))
                    except ValueError:
                        continue
    else:
        for line in git('reflog', '--date=iso-strict', '--format=%H %gd', 'HEAD'):
            match = re.match(r'([0-9a-f]{40}) HEAD@\{([^}]+)\}', line)
            if match and match[1] in chain:
                markers.append((epoch(match[2]), chain[match[1]]))
    return chain, markers


def github_samples(definitions, run_rows, prs, chain, markers, source, since, until, host='local', gateway='local'):
    for row in run_rows:
        if row.get('status') != 'completed' or row.get('conclusion') in ('skipped', 'neutral'):
            continue
        start, end = epoch(row.get('run_started_at')), epoch(row.get('updated_at'))
        if start is None or not since <= epoch(row['created_at']) <= until:
            continue
        for d in definitions:
            if d.get('workflow') == row.get('path'):
                result = sample(d['id'], (source, row['id'], row.get('run_attempt', 1)), start, end, host, gateway, run=row['id'])
                if result:
                    # Research assigns CI runs to the creation day, not start day.
                    result['start'] = epoch(row['created_at'])
                    yield result
    for pr in prs:
        opened, merged = epoch(pr['createdAt']), epoch(pr['mergedAt'])
        if not since <= merged <= until:
            continue
        commit = (pr.get('mergeCommit') or {}).get('oid')
        position = chain.get(commit)
        deployed = min((at for at, index in markers if position is not None and index <= position and at >= merged), default=None)
        for d in definitions:
            if d.get('workflow') or d.get('base', 'main') != pr.get('baseRefName', 'main'):
                continue
            start = opened if d['start'] == 'pr_opened' else merged
            end = merged if d['end'] == 'pr_merged' else deployed
            result = sample(d['id'], (source, pr['number']), start, end, host, gateway, pr=pr['number'])
            if result:
                # PR history uses merge day (and excludes unmatched deployments).
                result['start'] = merged
                yield result


def collect_github(root, store, definitions, config, since, until, host='local', gateway='local', fetch=api):
    total = 0
    for alias, settings in config.items():
        selected = [d for d in definitions if d['source'] == 'github.' + alias]
        if not selected:
            continue
        repo = settings['repo']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise ValueError('Invalid repository mapping')
        from .registry import fingerprint
        key = 'github:' + alias + ':' + fingerprint(selected)
        previous = store.cursor(key) or {}
        # Retry unfinished runs regardless of age; rescan new/changed recent days.
        recent = max(since, previous.get('at', since)-86400)
        fresh = runs(repo, recent, until, fetch) if any(d.get('workflow') for d in selected) else []
        pending = previous.get('pending', [])
        pending_rows = [fetch(f'repos/{repo}/actions/runs/{rid}') for rid in pending]
        run_rows = {(r['id'], r.get('run_attempt', 1)): r for r in [*pending_rows, *fresh]}
        prs = pull_requests(repo, since, fetch) if any(not d.get('workflow') for d in selected) else []
        checkout = settings.get('checkout')
        chain, markers = deployment_markers(root, Path(checkout).expanduser(), settings.get('deploy_kind', alias)) if checkout else ({}, [])
        samples = github_samples(selected, run_rows.values(), prs, chain, markers, alias, since, until, host, gateway)
        total += store.ingest(samples, key, {'at': until, 'pending': sorted({r['id'] for r in run_rows.values() if r['status'] != 'completed'})})
    return total
