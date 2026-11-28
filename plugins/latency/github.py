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


class GitHubReadError(RuntimeError):
    def __init__(self, status=None):
        super().__init__('GitHub read failed; cursor retained')
        self.status = status


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
            match = re.search(r'HTTP\s+(\d{3})', result.stderr)
            raise GitHubReadError(int(match[1]) if match else None)
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
        for log in sorted((Path(root) / 'logs').glob('hermes-sync.log*')):
            if not log.is_file() or log.suffix == '.gz':
                continue
            for line in log.read_text(errors='replace').splitlines():
                if 'core: update exit 0:' not in line:
                    continue
                match = re.search(r'✓ default \(pid \d+\) @ ([0-9a-f]+)', line)
                if not match:
                    continue
                hits = [index for sha, index in chain.items() if sha.startswith(match[1])]
                if len(hits) == 1:
                    try:
                        # sync log() emits host-local wall time, not UTC.
                        markers.append((datetime.fromisoformat(line[:19]).timestamp(), hits[0]))
                    except ValueError:
                        continue
    else:
        for line in git('reflog', '--date=iso-strict', '--format=%H %gd', 'HEAD'):
            match = re.match(r'([0-9a-f]{40}) HEAD@\{([^}]+)\}', line)
            if match and match[1] in chain:
                markers.append((epoch(match[2]), chain[match[1]]))
    return chain, markers


def latest_runs(rows):
    """A rerun revises one logical run; attempt is not a second observation."""
    found = {}
    for row in rows:
        old = found.get(row['id'])
        rank = lambda r: (r.get('run_attempt', 1), r.get('updated_at') or '')
        if old is None or rank(row) >= rank(old):
            found[row['id']] = row
    return found


def github_samples(definitions, run_rows, prs, chain, markers, source, since, until, host='local', gateway='local'):
    for row in latest_runs(run_rows).values():
        if row.get('status') != 'completed' or row.get('conclusion') not in ('success', 'failure'):
            continue
        start = epoch(row.get('run_started_at') or row.get('created_at'))
        end = epoch(row.get('updated_at'))
        if start is None or not since <= start <= until:
            continue
        for d in definitions:
            if d.get('workflow') and (row.get('path') or '').endswith(d['workflow']):
                result = sample(d['id'], (source, row['id']), start, end, host, gateway, run=row['id'])
                if result:
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
        key = 'github:' + alias + ':' + fingerprint(selected) + ':events-v2'
        previous = store.cursor(key) or {}
        # Retry unfinished runs regardless of age; rescan new/changed recent days.
        recent = max(since, previous.get('at', since)-86400)
        ci = [d for d in selected if d.get('workflow')]
        fresh = runs(repo, recent, until, fetch) if ci else []
        # Rotate one older creation-day per cycle to discover reruns of finished
        # runs, which neither a recent-day overlap nor pending IDs can discover.
        sweep = max(since, previous.get('sweep', since))
        if ci and previous and sweep < recent:
            fresh += runs(repo, sweep, min(sweep+86400,recent), fetch)
        next_sweep = sweep+86400 if sweep+86400 < recent else since
        pending = previous.get('pending', [])
        pending_rows = []
        for rid in pending:
            try:
                pending_rows.append(fetch(f'repos/{repo}/actions/runs/{rid}'))
            except GitHubReadError as error:
                if error.status != 404:
                    raise
                # Deleted/expired runs have no terminal duration to aggregate.
        run_rows = latest_runs([*pending_rows, *fresh])
        prs = pull_requests(repo, since, fetch) if any(not d.get('workflow') for d in selected) else []
        checkout = settings.get('checkout')
        chain, markers = deployment_markers(root, Path(checkout).expanduser(), settings.get('deploy_kind', alias)) if checkout else ({}, [])
        rows = list(github_samples(selected, run_rows.values(), prs, chain, markers, alias, since, until, host, gateway))
        ci_ids = {d['id'] for d in ci}
        total += store.ingest([s for s in rows if s['metric'] not in ci_ids])
        cursor = {'at': until, 'pending': sorted({r['id'] for r in run_rows.values() if r['status'] != 'completed'}),
                  'sweep': next_sweep}
        if ci:
            observed = [sample(d['id'], (alias,rid), 0, 0)['id'] for rid in set(run_rows) | set(pending) for d in ci]
            total += store.reconcile([s for s in rows if s['metric'] in ci_ids],ci_ids,key,cursor,observed)
        else:
            store.ingest([],key,cursor)
    return total
