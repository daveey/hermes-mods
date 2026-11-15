"""Replay benchmark for card L (design t_01a1d563324d7e8e §3): the runners inventory budget.

P1 measured the fleet panel's inventory reads at 0.8% of one core in the busiest serve: on main every
``_ci_section`` call decoded the 2.4 MB snapshot twice (inventory and host load) and deep-copied every runner row.
This replays the hub's measured change shape (hub 2026-11-13, 1.9k runners: the collector rewrites the snapshot every
~30 s; between snapshots ``processed_at`` moves on all runner states, ~5% are re-observed, and the host
result moves every other snapshot) with a fleet build every FLEET_CACHE_S = 5 s. It measures thread CPU of main's
per-call work and of the card L path over the same replay. The absolute thread CPU / replay duration must
remain within 0.1% of one core on deployment-class hardware (INVENTORY-BUDGET.md).
The historical P1-scaled work ratio runs on hosted CI: decoded bytes and projected
runner rows each must retain the eightfold reduction, without noisy CPU ratios.
This is a deterministic regression guard, not absolute CPU budget proof.
The fixture includes 556 scope/coverage/provenance entries and 221 source errors, matching the measured
post-deploy cardinalities, with fictional identifiers only."""
import pytest
import json
import os
import sqlite3
import time
from copy import deepcopy
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from plugins.runners import service as service_module
from plugins.runners.collector import Collector, iso
from plugins.runners.providers.base import ProviderResult
from plugins.runners.service import Service
from plugins.runners.store import Store
from test_collector import F, config

P1_NOW = 0.8  # % of one core, card A's P1 snapshot
BUDGET = 0.1
RUNNERS = int(os.environ.get('RUNNERS_BENCH_RUNNERS', '1900'))
CALL_EVERY, WRITE_EVERY, SPAN, LIVE = 5, 30, 600, 0.05


@contextmanager
def count_work(work):
    """Observe actual decoder inputs and row projections, never fixture creation.

    Keep timing-only replays uninstrumented: wrappers are for deterministic CI
    regression checks, not absolute CPU evidence.
    """
    if work is None:
        yield
        return
    work.update(decoded_bytes=0, projected_rows=0)

    def decoder(original):
        def counted(payload, *args, **kwargs):
            work['decoded_bytes'] += len(payload.encode('utf-8') if isinstance(payload, str) else payload)
            return original(payload, *args, **kwargs)
        return counted

    original_project = service_module.project

    def project(*args, **kwargs):
        work['projected_rows'] += 1
        return original_project(*args, **kwargs)

    with ExitStack() as stack:
        stack.enter_context(patch.object(json, 'loads', decoder(json.loads)))
        if service_module.msgspec is not None:
            stack.enter_context(patch.object(service_module.msgspec.json, 'decode',
                                            decoder(service_module.msgspec.json.decode)))
        stack.enter_context(patch.object(service_module, 'project', project))
        yield


class Fleet:
    def enumerate(self, scope):
        rows = tuple(dict(F['Runner'], id=f"{F['Runner']['id'][:-6]}{i:06d}", provider_runner_id=str(i + 1))
                     for i in range(RUNNERS))
        return ProviderResult(rows, True, None, 1000.0, 1)


def snapshots(path, cfg, scattered=False):
    """The replay: (time, payload) every WRITE_EVERY s, starting from a real collector snapshot."""
    with Store(path) as store:
        snap = Collector(store, cfg, Fleet(), clock=lambda: 1000.0, host_probe=lambda host: {}).tick()
    snap = json.loads(json.dumps(snap))
    # Public fictional replay of the measured multi-scope shape: preserve the
    # collector's nested scope result/coverage/provenance duplication, not padding.
    template = next(iter(snap['scope_results'].values()))
    scopes, coverage, provenance, errors, results = [], [], [], [], {}
    for i in range(556):
        sid = f'scope_{i + 1:016x}'
        scopes.append(dict(cfg['scopes'][0], id=sid, name=f'example/project-{i}'))
        result = deepcopy(template)
        result['coverage'].update(scope_id=sid, enumerated_count=RUNNERS // 556 + (i < RUNNERS % 556),
                                  provider_total_count=RUNNERS // 556 + (i < RUNNERS % 556))
        result['provenance']['source_id'] = sid
        if i < 221:
            result.update(error='forbidden', complete=False)
            result['coverage'].update(state='partial', pagination_complete=False, reason_code='forbidden')
            result['provenance']['state'] = 'unavailable'
            errors.append(dict(source_id=sid, code='forbidden', retry_after_seconds=None,
                               observed_at=result['provenance']['observed_at']))
        results[sid] = result
        coverage.append(deepcopy(result['coverage']))
        provenance.append(deepcopy(result['provenance']))
    cfg['scopes'] = scopes
    snap.update(scope_results=results, coverage=coverage, provenance=provenance, source_errors=errors)
    for i, row in enumerate(snap['runners']):
        row['scope_id'] = scopes[i % len(scopes)]['id']
    hid = cfg['hosts'][0]['id']
    snap.setdefault('host_results', {})[hid] = dict(at=1000.0, management='reachable', facts={}, observation_ids=[])
    ids = sorted(snap['states'])
    live_count = max(1, int(len(ids) * LIVE))
    live = ids[:live_count] if not scattered else [ids[i * len(ids) // live_count] for i in range(live_count)]
    out = []
    for n, t in enumerate(range(0, SPAN, WRITE_EVERY)):
        at = 1000.0 + t
        snap['at'] = at
        cycle = f'collection_{n + 1:016x}'
        for rid in ids:
            snap['states'][rid].update(processed_at=at, cycle=cycle)
        for rid in live:
            snap['states'][rid]['at'] = at
        live_ids = set(live)
        refreshed = set()
        for row in snap['runners']:
            if row['id'] in live_ids:
                row['observation_ids'] = [f"observation_{n:04x}{int(row['provider_runner_id']):012x}"]
                refreshed.add(row['scope_id'])
        ttl = cfg['thresholds']['inventory_stale_seconds']
        for sid in refreshed:
            result = snap['scope_results'][sid]
            result.update(observed_at=at, served_at=at)
            result['provenance'].update(collection_id=cycle, observed_at=iso(at), expires_at=iso(at + ttl))
        snap['provenance'] = [deepcopy(r['provenance']) for r in snap['scope_results'].values()]
        for error in snap['source_errors']:
            error['observed_at'] = snap['scope_results'][error['source_id']]['provenance']['observed_at']
        if n % 2 == 0:
            snap['host_results'][hid]['at'] = at
        out.append((t, json.dumps(snap)))
    return out


def replay(tmp_path, card_l, run=0, scattered=False, work=None):
    cfg = config(); cfg['hosts'] = [deepcopy(F['Host'])]
    path = tmp_path / f"{'new' if card_l else 'old'}{run}" / 'state.sqlite'
    path.parent.mkdir()
    seq = snapshots(path, cfg, scattered=scattered)
    fingerprint = Service.config_fingerprint(cfg)  # once per parsed config, as in the dashboard
    db = sqlite3.connect(path)
    cpu, i = 0.0, 0
    with count_work(work), patch('plugins.runners.service.time.monotonic', lambda: float(t)):
        for t in range(0, SPAN, CALL_EVERY):
            while i < len(seq) and seq[i][0] <= t:
                db.execute("UPDATE collector_cache SET payload=? WHERE key='snapshot'", (seq[i][1],)); db.commit(); i += 1
            now = 1000.0 + t + 3
            # Advance BOTH clocks: the read-cache ceiling must not see a compressed replay.
            svc = Service(path, cfg, clock=lambda: now, fingerprint=fingerprint if card_l else None)
            start = time.thread_time()
            if card_l:
                inv = svc.inventory(shared=True)
                svc._snapshot()
            else:  # main: decode, deep build, decode again for host load
                payload = db.execute("SELECT payload FROM collector_cache WHERE key='snapshot'").fetchone()[0]
                inv = svc._inventory(json.loads(payload), None)
                json.loads(payload)
            cpu += time.thread_time() - start
            assert len(inv['data']['runners']) == RUNNERS
    db.close()
    return cpu


@pytest.mark.parametrize('scattered', [False, True], ids=['clustered', 'scattered'])
def test_inventory_replay_budget(tmp_path, scattered):
    # Enforce the same eightfold reduction independently for both dominant work
    # classes. No arbitrary weighting or assumption about the runner's CPU speed.
    # This is a work-regression gate, NOT absolute CPU proof (see the gate below).
    old, new = {}, {}
    replay(tmp_path, card_l=False, scattered=scattered, work=old)
    replay(tmp_path, card_l=True, scattered=scattered, work=new)
    assert_work_budget(old, new)
    # The actual legacy replay, substituted as "new", must fail this same gate.
    with pytest.raises(AssertionError, match='work budget'):
        assert_work_budget(old, old)


def assert_work_budget(old, new):
    for metric in ('decoded_bytes', 'projected_rows'):
        assert old[metric] > 0 and new[metric] > 0
        share = P1_NOW * new[metric] / old[metric]
        print(f'{metric}: old={old[metric]}, new={new[metric]}, '
              f'P1-scaled work={share:.6f}% (budget {BUDGET}%)')
        assert share <= BUDGET, f'{metric} work budget exceeded: {share} > {BUDGET}'


@pytest.mark.parametrize('scattered', [False, True], ids=['clustered', 'scattered'])
def test_inventory_replay_absolute_budget(tmp_path, scattered):
    # Absolute CPU is a deployment-hardware contract, not a hosted-runner speed
    # contract. The relative test above ALWAYS runs. Before landing, run
    # scripts/check_inventory_hardware.py on trusted deployment-class hardware.
    # Require mode takes precedence: that command must never silently skip.
    if (os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('RUNNERS_BENCH_REQUIRE_ABSOLUTE') != '1'):
        pytest.skip('absolute 0.1% gate requires separate exact-head hardware evidence')
    assert RUNNERS == 1900, 'absolute acceptance requires 1900 runners'
    samples = [replay(tmp_path, card_l=True, run=n, scattered=scattered) for n in range(2)]
    new = min(samples)
    print(f'\nabsolute replay: samples={samples}, span={SPAN}, runners={RUNNERS}, '
          f'scattered={scattered}, core_percent={new / SPAN * 100:.6f}')
    assert new / SPAN * 100 <= BUDGET, 'absolute inventory CPU exceeds the P1 budget'
