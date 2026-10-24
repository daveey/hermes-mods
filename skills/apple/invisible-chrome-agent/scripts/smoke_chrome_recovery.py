"""Linux acceptance of durable supervisor reconciliation against a real child.

This restarts the Python supervisor/ledger objects, not launchd or vdisplay.
"""
import time
from unittest import mock

from chrome_agent_lifecycle import Ledger, Lifecycle


def reconcile(api, ledger, lifecycle, profile, child, upstream):
    version = api.cdp_version
    original = api.Supervisor()
    original.chrome = child
    original.lifecycle = lifecycle
    with mock.patch.object(api, 'PROFILE', str(profile)), \
         mock.patch.object(api, 'cdp_version', side_effect=lambda: version(port=upstream)):
        original.remember_browser()
        with ledger.transaction(readonly=True) as data:
            generation = data['generation']
            requests = {cap: dict(lease['requests']) for cap, lease in data['leases'].items()}
        for connection in list(lifecycle.connections):
            connection.close()
        replacement = Ledger(ledger.path, ledger.config)
        resumed = Lifecycle(replacement, lifecycle.installation_id, lifecycle.wake, lifecycle.stop)
        supervisor = api.Supervisor()
        supervisor.lifecycle = resumed
        # Virtual display only exists on Mac; no fake Chrome/CDP in this probe.
        supervisor.start_vdisplay = lambda: None
        assert supervisor.recover_managed()
        assert supervisor.chrome.pid == child.pid and supervisor.chrome.poll() is None
        with replacement.transaction(readonly=True) as data:
            assert data['generation'] == generation
            assert {cap: lease['requests'] for cap, lease in data['leases'].items()} == requests
        print('PASS real surviving Chrome: new supervisor/ledger reconciles exact process and generation, requests retained', flush=True)
        return replacement, resumed


def repeated_show(api, ledger, lifecycle, client, root, target):
    show = root / 'show'
    observer = api.Supervisor()
    observer.lifecycle = lifecycle
    observer.cdp = client
    with mock.patch.object(api, 'SHOW_FLAG', str(show)):
        for episode in range(2):
            show.write_text(str(time.time() + 5))
            observer.lifecycle_tick()
            with ledger.transaction(readonly=True) as data:
                protection = dict(data['targets'][target]['protections']['handoff'])
            observer.lifecycle_tick()
            with ledger.transaction(readonly=True) as data:
                assert data['targets'][target]['protections']['handoff'] == protection
            if episode:
                assert protection['episode'] != previous['episode']
                assert protection['until'] > previous['until']
            previous = protection
            time.sleep(.02)
        show.unlink()
    # Wait out the genuine short show bound rather than bypassing protection.
    time.sleep(5.1)
    print('PASS real scoped targets: repeated show renews by episode, polling does not, short show deadline expires', flush=True)
