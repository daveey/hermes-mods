"""Production home partitions: one shared child, bounded per-home producers, server-side binding and revocation.

Normative H gate: partition by canonical home + lifecycle/tombstone + authorization generation (never labels),
equal-label homes cannot collide, aliases share only an identical authorized home, and a roster/revocation/
recreation invalidates queued demands, bytes and events before another reply.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

import test_explicit_home_acquisition as helpers
from test_explicit_home_context import ROOT, load_module, seed_home

homes = pytest.importorskip('hermes_cli.serve_producer_home')
runtime_mod = pytest.importorskip('hermes_cli.serve_producer_runtime')
broker = pytest.importorskip('hermes_cli.serve_producer_broker')
api = helpers.api


class Authority:
    def __init__(self, home, epoch=1):
        self.profile_id = str(home)
        self.instance_id = 'inst'
        self.epoch = epoch
        self.retiring = False
        self.retired = False
        self.db = SimpleNamespace(db_path=str(home / 'state.db'))


def state_for(*authorities, launch):
    from gateway.session_authorities import SessionAuthorities
    registry = SessionAuthorities(launch)
    registry.add(launch, None)
    for authority in authorities:
        registry.add(authority.profile_id, authority)
    return SimpleNamespace(gateway_runner=SimpleNamespace(session_authorities=registry))


def solo_state(authority):
    return SimpleNamespace(session_authority=authority)


@pytest.fixture
def provider_mod(api):
    return load_module(ROOT / 'dashboard' / 'producer_provider.py', '_partition_provider')


@pytest.fixture
def twins(tmp_path):
    """Two homes with the SAME profile label under different roots, plus a symlink alias of the first."""
    first = tmp_path / 'a' / '.hermes' / 'profiles' / 'researcher'
    second = tmp_path / 'b' / '.hermes' / 'profiles' / 'researcher'
    (first.parent).mkdir(parents=True)
    (second.parent).mkdir(parents=True)
    seed_home(first, session='chat', marker='MARK-A')
    seed_home(second, session='chat', marker='MARK-B')
    alias = tmp_path / 'alias-of-a'
    alias.symlink_to(first)
    return {'a': first, 'b': second, 'alias': alias}


def binding(home, epoch=1):
    return homes.bind(home, solo_state(Authority(homes._canonical(home), epoch)))


def request_payload(bound, sessions=('chat',)):
    body = {'sessions': list(sessions), '_wire_identity': {'feed': 'fixture:serve', 'profile': 'researcher'},
            '_partition': bound.wire()}
    return {'body': body, 'accepted': time.monotonic() - 1}


class StubProducer:
    def __init__(self, label):
        self.label = label
        self.closed = False
        self.seen = []

    def __call__(self, payload):
        self.seen.append(payload)
        return b'{"label":"%s"}' % self.label.encode()

    def events_since(self, after, limit=64):
        return {'seq': 0, 'events': [], 'reset': False, 'label': self.label}

    def close(self):
        self.closed = True


def stub_set(provider_mod, api_mod):
    made = []

    def factory(bound):
        stub = StubProducer(f'{len(made)}:{bound.home}')
        made.append((bound, stub))
        return stub
    return provider_mod.PanelProducerSet(api_mod, factory=factory), made


def test_open_provider_selects_the_partition_set_only_for_a_partitioned_spec(provider_mod):
    one = provider_mod.open_provider({'provider': {'partitioned': True}})
    try:
        assert isinstance(one, provider_mod.PanelProducerSet)
    finally:
        one.close()
    plain = provider_mod.open_provider({'provider': {}})
    try:
        assert not isinstance(plain, provider_mod.PanelProducerSet)
    finally:
        plain.close()


def test_missing_or_malformed_claims_are_refused(provider_mod, api, twins):
    pset, made = stub_set(provider_mod, api)
    good = binding(twins['a'])
    with pytest.raises(ValueError, match='partition required'):
        pset({'body': {'sessions': ['chat']}, 'accepted': 0})
    with pytest.raises(ValueError, match='partition required'):
        pset.events_since(None)
    for bad in ({}, {'home': 'x'}, dict(good.wire(), extra=1), 'home', 7):
        with pytest.raises(ValueError, match='invalid home partition'):
            pset(request_payload(good) | {'body': dict(request_payload(good)['body'], _partition=bad)})
    assert made == [] and pset.partitions() == []


def test_one_producer_per_identity_alias_shares_and_equal_labels_do_not_collide(provider_mod, api, twins):
    pset, made = stub_set(provider_mod, api)
    a, b, alias = binding(twins['a']), binding(twins['b']), binding(twins['alias'])
    assert alias == a and a.identity != b.identity
    one = pset(request_payload(a))
    two = pset(request_payload(b))
    again = pset(request_payload(alias))
    assert one == again and one != two
    assert len(made) == 2 and len(pset.partitions()) == 2
    # The claim is consumed by the partition router; the producer never sees it.
    assert all('_partition' not in stub.seen[0]['body'] for _, stub in made)
    assert pset.events_since(None, partition=a.wire())['label'] == made[0][1].label
    assert pset.events_since(None, partition=b.wire())['label'] == made[1][1].label


def test_a_newer_generation_of_the_same_home_closes_the_older_one(provider_mod, api, twins):
    pset, made = stub_set(provider_mod, api)
    old = binding(twins['a'], epoch=1)
    new = binding(twins['a'], epoch=2)
    assert old.authorization_generation != new.authorization_generation
    pset(request_payload(old))
    pset(request_payload(new))
    assert [stub.closed for _, stub in made] == [True, False]
    assert pset.partitions() == [new.identity]


def test_stale_lifecycle_claim_is_refused_and_closes_that_home(provider_mod, api, twins):
    pset, made = stub_set(provider_mod, api)
    live = binding(twins['a'])
    pset(request_payload(live))
    stale = homes.HomeBinding(live.home, 'f' * 64, live.authorization_generation)
    with pytest.raises(RuntimeError, match='stale home generation'):
        pset(request_payload(stale))
    assert made[0][1].closed and pset.partitions() == []


def test_tombstoned_home_is_revoked_and_closed(provider_mod, api, twins):
    from hermes_constants import mark_named_profile_deleted
    pset, made = stub_set(provider_mod, api)
    live = binding(twins['a'])
    pset(request_payload(live))
    mark_named_profile_deleted(twins['a'])
    with pytest.raises(RuntimeError, match='home revoked'):
        pset(request_payload(live))
    assert made[0][1].closed and pset.partitions() == []


def test_partitions_are_bounded_and_least_recently_used_is_evicted(provider_mod, api, tmp_path):
    pset, made = stub_set(provider_mod, api)
    bindings = []
    for index in range(provider_mod.PanelProducerSet.MAX_PARTITIONS + 1):
        home = tmp_path / f'h{index}' / '.hermes' / 'profiles' / 'p'
        seed_home(home, session='chat', marker=f'M{index}')
        bindings.append(binding(home))
    for index in range(provider_mod.PanelProducerSet.MAX_PARTITIONS):
        pset(request_payload(bindings[index]))
    pset(request_payload(bindings[0]))  # touch: the oldest is now index 1
    pset(request_payload(bindings[-1]))
    assert len(pset.partitions()) == provider_mod.PanelProducerSet.MAX_PARTITIONS
    assert made[1][1].closed and not made[0][1].closed
    assert bindings[1].identity not in pset.partitions()


def test_real_producers_return_only_their_own_homes_data(provider_mod, api, twins):
    """Real PanelProducer partitions: equal-label homes under different roots never share bundles or readers."""
    pset = provider_mod.open_provider({'provider': {'partitioned': True}})
    a, b = binding(twins['a']), binding(twins['b'])
    try:
        wire_a = helpers.populated(pset, lambda: request_payload(a))
        wire_b = helpers.populated(pset, lambda: request_payload(b))
        text_a, text_b = wire_a.decode(), wire_b.decode()
        assert 'MARK-A' in text_a and 'MARK-B' not in text_a
        assert 'MARK-B' in text_b and 'MARK-A' not in text_b
        assert json.loads(text_a)['bundles'] and json.loads(text_b)['bundles']
        assert len(pset.partitions()) == 2
        assert {p.readers.home_context.home for p in pset._parts.values()} == {
            homes._canonical(twins['a']), homes._canonical(twins['b'])}
    finally:
        pset.close()
    assert pset.partitions() == []


# ---- the serve side: server-bound relay, per-request re-derivation, atomic revocation ----

def make_relay(api_mod, tmp_path, bound):
    relay = runtime_mod.ProjectionRelay(
        api_mod._panel_provider_spec(), home=tmp_path, runtime=tmp_path / 'runtime', generation='g',
        initial=api_mod.panel_initial_payload(bound))
    relay.bind_partition(bound)
    return relay


class Request:
    def __init__(self, app_state):
        self.app = SimpleNamespace(state=app_state)


@pytest.fixture
def served(monkeypatch, api, twins, tmp_path):
    """A secondary profile served through a real session-authority registry; the plugin serves HERMES_HOME."""
    home = twins['a']
    monkeypatch.setenv('HERMES_HOME', str(home))
    authority = Authority(homes._canonical(home))
    state = state_for(authority, launch=tmp_path)
    api._LOADER.external_projection = None
    yield SimpleNamespace(api=api, home=home, authority=authority, state=state, request=Request(state))
    api._LOADER.external_projection = None
    api._LOADER.__dict__.pop('app_state', None)


def test_route_rederives_the_binding_and_revokes_before_another_reply(served, tmp_path):
    api_mod = served.api
    first = api_mod.current_binding(served.state)
    relay = make_relay(api_mod, tmp_path, first)
    api_mod._LOADER.external_projection = relay
    assert api_mod._bound_external(served.request) is relay
    assert relay.partition_identity == first.identity
    key = relay.submit({'body': {'_verb': 'status', '_wire_identity': {'feed': 'f', 'profile': 'p'}}, 'accepted': 0})
    assert relay.pending_count == 1
    served.authority.epoch += 1  # roster/authorization change
    assert api_mod._bound_external(served.request) is relay
    second = api_mod.current_binding(served.state)
    assert relay.partition_identity == second.identity != first.identity
    assert relay.pending_count == 0 and relay.read(key) is None  # queued work and bytes revoked
    served.authority.retiring = True  # the home is no longer served at all
    api_mod._bound_external(served.request)
    assert relay.partition_identity is None
    with pytest.raises(runtime_mod.ResourceExhausted):
        relay.submit({'body': {'_verb': 'status', '_wire_identity': {'feed': 'f', 'profile': 'p'}}, 'accepted': 0})
    response = runtime_mod.projection_response(relay, {'_verb': 'status', '_wire_identity': {'feed': 'f', 'profile': 'p'}})
    assert response.status_code == 503 and response.headers['Retry-After'] == '1'
    served.authority.retiring = False  # re-authorized: a fresh binding, never the revoked bytes
    api_mod._bound_external(served.request)
    assert relay.partition_identity == api_mod.current_binding(served.state).identity


def test_tombstone_and_recreation_revoke_the_serve_binding(served, tmp_path):
    from hermes_constants import mark_named_profile_deleted
    api_mod = served.api
    relay = make_relay(api_mod, tmp_path, api_mod.current_binding(served.state))
    api_mod._LOADER.external_projection = relay
    api_mod._bound_external(served.request)
    assert relay.partition_identity is not None
    mark_named_profile_deleted(served.home)
    api_mod._bound_external(served.request)
    assert relay.partition_identity is None


def test_route_without_state_uses_the_serve_launch_authority_only(monkeypatch, api, twins, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(twins['a']))
    bound = api.current_binding()  # standalone: its own launch home
    assert bound.home == str(homes._canonical(twins['a']))
    relay = make_relay(api, tmp_path, bound)
    api._LOADER.external_projection = relay
    try:
        api._bound_external(None)
        assert relay.partition_identity == bound.identity
        # A claim for another home is never current for this serve: no label or request can authorize it.
        other = binding(twins['b'])
        assert not api._partition_still_current(other.identity)
        assert api._partition_still_current(bound.identity)
    finally:
        api._LOADER.external_projection = None


def test_unpartitioned_external_relay_is_left_untouched(api, tmp_path):
    spec = dict(api._panel_provider_spec())
    spec.pop('partitioned')
    relay = runtime_mod.ProjectionRelay(spec, home=tmp_path, runtime=tmp_path / 'r', generation='g')
    api._LOADER.external_projection = relay
    try:
        assert api._bound_external(None) is relay
        assert not relay.partitioned and relay.partition_identity is None
    finally:
        api._LOADER.external_projection = None


def test_lifecycle_installation_is_the_hermes_root_of_the_served_home(monkeypatch, api, twins, tmp_path):
    from hermes_constants import get_default_hermes_root
    monkeypatch.setenv('HERMES_HOME', str(twins['a']))
    assert get_default_hermes_root(home=twins['a']) == twins['a'].parents[1]
    monkeypatch.delenv('HERMES_SERVE_PANEL_EXTERNAL', raising=False)
    monkeypatch.delenv('HERMES_SERVE_PRODUCERS_EXTERNAL', raising=False)
    lifecycle = api.install_panel_lifecycle({})
    assert lifecycle is not None
    try:
        assert str(lifecycle.installation) == str(twins['a'].parents[1])
    finally:
        lifecycle.stop_watching()
        assert api._retire_legacy_panel()


def test_request_home_override_never_changes_the_serve_home(api, twins, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    monkeypatch.setenv('HERMES_HOME', str(twins['a']))
    before = api.current_binding()
    token = set_hermes_home_override(twins['b'])
    try:
        assert api.current_binding() == before
        assert api._serve_home() == twins['a']
    finally:
        reset_hermes_home_override(token)


def test_initial_and_factory_implicit_binding(api, twins, tmp_path, monkeypatch):
    assert '_partition' not in api.panel_initial_payload()['body']
    bound = binding(twins['a'])
    calls = []
    monkeypatch.setattr(api, 'current_binding', lambda: calls.append(True) or bound)
    relay = api.make_panel_relay(tmp_path, tmp_path / 'runtime', 'g')
    assert calls == [True]
    assert relay.partition_identity == bound.identity
    api._LOADER.external_projection = None
    assert api._bound_external(None) is None


def test_factory_preserves_explicit_binding_without_resolving_again(api, twins, tmp_path, monkeypatch):
    bound = binding(twins['a'])
    def forbidden(*args):
        pytest.fail('explicit binding must not be resolved from ambient state')
    monkeypatch.setattr(api, 'current_binding', forbidden)
    relay = api.make_panel_relay(tmp_path, tmp_path / 'runtime', 'g', binding=bound)
    assert relay.partition_identity == bound.identity
    assert relay.config['initial']['body']['_partition'] == bound.wire()


def test_request_state_is_retained_for_background_checks_and_stateless_calls(served, tmp_path):
    api_mod = served.api
    relay = make_relay(api_mod, tmp_path, api_mod.current_binding(served.state))
    api_mod._LOADER.external_projection = relay
    api_mod._bound_external(served.request)
    assert api_mod._LOADER.app_state is served.state
    before = relay.partition_identity
    api_mod._bound_external(None)
    assert api_mod._LOADER.app_state is served.state  # absent request never clears the authority
    assert relay.partition_identity == before
    assert api_mod._partition_still_current(before)
    served.authority.retiring = True
    assert not api_mod._partition_still_current(before)
    api_mod._bound_external(None)
    assert relay.partition_identity is None


@pytest.mark.parametrize('config', [None, [], 'bad', {}, {'provider': None}, {'provider': []}])
def test_unpartitioned_nonmapping_config_is_safe(provider_mod, monkeypatch, config):
    sentinel = object()
    monkeypatch.setattr(provider_mod, 'PanelProducer', lambda *args, **kwargs: sentinel)
    assert provider_mod.open_provider(config) is sentinel


def test_bare_body_dispatch_and_malformed_payloads(provider_mod, api, twins):
    pset, made = stub_set(provider_mod, api)
    bound = binding(twins['a'])
    pset({'_partition': bound.wire(), 'sessions': ['chat']})
    assert made[0][1].seen == [{'sessions': ['chat']}]
    for bad in (None, [], 'bad', {'body': None}, {'body': []}):
        with pytest.raises(ValueError, match='partition required'):
            pset(bad)


def test_noncanonical_claim_revokes_only_its_own_home(provider_mod, api, twins):
    pset, made = stub_set(provider_mod, api)
    a, b = binding(twins['a']), binding(twins['b'])
    pset(request_payload(a))
    pset(request_payload(b))
    forged = homes.HomeBinding(str(twins['alias']), a.lifecycle_generation, a.authorization_generation)
    with pytest.raises(RuntimeError, match='stale home generation'):
        pset(request_payload(forged))
    assert pset.partitions() == [a.identity, b.identity]
    # Revoking a itself must not close the unrelated b partition.
    stale = homes.HomeBinding(a.home, 'wrong-life', a.authorization_generation)
    with pytest.raises(RuntimeError, match='stale home generation'):
        pset(request_payload(stale))
    assert pset.partitions() == [b.identity]
    assert made[0][1].closed and not made[1][1].closed


def test_new_generation_replaces_only_its_own_home(provider_mod, api, twins):
    pset, made = stub_set(provider_mod, api)
    a, b = binding(twins['a']), binding(twins['b'])
    pset(request_payload(a))
    pset(request_payload(b))
    newer = binding(twins['a'], epoch=2)
    pset(request_payload(newer))
    assert pset.partitions() == [b.identity, newer.identity]
    assert made[0][1].closed and not made[1][1].closed


# ---- real exec: ONE shared child serves two equal-label homes through two serves ----

def _complete_cut(gate, installation):
    from hermes_cli.serve_producer_admission import REQUIRED_COLLECTORS, AdmissionCut, CollectorEvidence
    collectors = tuple((n, CollectorEvidence((f'{n}:ok',), ())) for n in sorted(REQUIRED_COLLECTORS))
    return AdmissionCut(boot_id='boot', generation='a' * 64, installation=str(installation), census=None,
                        collectors=collectors, classifications=(), legacy_candidates=(), retired=(),
                        errors=(), _acquisition=gate._acquisition)


def test_real_exec_shared_child_isolates_equal_label_homes_and_revokes(api, twins, tmp_path, monkeypatch):
    from hermes_cli.serve_producer_admission import LaunchGate
    from tests.waiting import wait_until
    install = tmp_path / 'install'
    install.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(install))
    monkeypatch.setenv('HOME', str(tmp_path))
    runtime = install / 'runtime'
    a, b = binding(twins['a']), binding(twins['b'])
    relays = []

    def attach(bound):
        relay = runtime_mod.ProjectionRelay(
            api._panel_provider_spec(), home=install, runtime=runtime, generation='unused',
            initial=api.panel_initial_payload(a))  # the spawner's own claim; joiners ignore it
        relay.bind_partition(bound)
        gate = LaunchGate.acquire(runtime)
        try:
            relay.start_for_production(cut=_complete_cut(gate, install), gate=gate, ready_timeout=90)
        finally:
            gate.close()
        relays.append(relay)
        return relay

    def ask(relay, who, marker):
        # Lanes own computation: the first answer can precede the bundle, so ask again until it carries the data.
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            key = relay.submit({'body': {'sessions': ['chat'], '_wire_identity': {'feed': 'fx', 'profile': who}},
                                'accepted': time.monotonic() - 1})
            assert wait_until(lambda: relay.read(key) is not None, timeout=60)
            text = relay.read(key).decode()
            if marker in text:
                return text
            time.sleep(0.2)
        raise AssertionError('no data for ' + marker)

    try:
        first, second = attach(a), attach(b)
        assert first.broker_pid == second.broker_pid
        assert first.ping()['child_pid'] == second.ping()['child_pid']
        text_a = ask(first, 'researcher', 'MARK-A')
        text_b = ask(second, 'researcher', 'MARK-B')
        assert 'MARK-B' not in text_a and 'MARK-A' not in text_b
        # A caller-supplied claim is overwritten by the relay's own: the browser cannot name another home.
        forged = {'body': {'sessions': ['chat'], '_wire_identity': {'feed': 'fx', 'profile': 'researcher'},
                           '_partition': b.wire()}, 'accepted': time.monotonic() - 1}
        text = ask(first, 'researcher', 'MARK-A')  # same claim ride as `forged`: still the relay's own home
        key = first.submit(forged)
        assert wait_until(lambda: first.read(key) is not None, timeout=60)
        assert 'MARK-B' not in first.read(key).decode() and 'MARK-B' not in text
        # Revocation: unbinding drops the queued work and bytes at once; nothing is served until rebound.
        pending = first.submit({'body': {'sessions': ['chat'], '_wire_identity': {'feed': 'fx', 'profile': 'r2'}},
                                'accepted': time.monotonic() - 1})
        first.unbind_partition()
        assert first.read(pending) is None and first.pending_count == 0
        with pytest.raises(runtime_mod.ResourceExhausted):
            first.submit(forged)
    finally:
        for relay in relays[1:]:
            relay.close(timeout=10)
        if relays:
            assert relays[0].retire(timeout=30)
    assert broker.read_owner(runtime) is None


def test_real_exec_event_attach_is_partitioned_and_revoked_with_the_binding(api, twins, tmp_path, monkeypatch):
    """The first event poll of each serve is reset+snapshot of ITS home only; a binding change stops delivery."""
    from hermes_cli.serve_producer_admission import LaunchGate
    from tests.waiting import wait_until
    install = tmp_path / 'install'
    install.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(install))
    monkeypatch.setenv('HOME', str(tmp_path))
    runtime = install / 'runtime'
    a, b = binding(twins['a']), binding(twins['b'])
    relays, seen = [], {}

    def attach(name, bound):
        relay = runtime_mod.ProjectionRelay(
            api._panel_provider_spec(), home=install, runtime=runtime, generation='unused',
            initial=api.panel_initial_payload(a))
        relay.bind_partition(bound)
        gate = LaunchGate.acquire(runtime)
        try:
            relay.start_for_production(cut=_complete_cut(gate, install), gate=gate, ready_timeout=90)
        finally:
            gate.close()
        relays.append(relay)
        seen[name] = []
        return relay

    try:
        first, second = attach('a', a), attach('b', b)
        for relay, who in ((first, 'researcher'), (second, 'researcher')):  # demand each home's bundle first
            key = relay.submit({'body': {'sessions': ['chat'], '_wire_identity': {'feed': 'fx', 'profile': who}},
                                'accepted': time.monotonic() - 1})
            assert wait_until(lambda relay=relay, key=key: relay.read(key) is not None, timeout=60)
        first.set_event_sink(seen['a'].append)
        second.set_event_sink(seen['b'].append)
        assert wait_until(lambda: seen['a'] and seen['b'], timeout=60)
        assert seen['a'][0].get('reset') is True and seen['b'][0].get('reset') is True
        assert 'MARK-B' not in json.dumps(seen['a'][0]) and 'MARK-A' not in json.dumps(seen['b'][0])
        delivered = len(seen['a'])
        first.unbind_partition()  # roster revocation: no further event of that home is requested or delivered
        time.sleep(1.0)
        assert len(seen['a']) == delivered
        assert first.event_polls >= 1
    finally:
        for relay in relays[1:]:
            relay.close(timeout=10)
        if relays:
            assert relays[0].retire(timeout=30)
