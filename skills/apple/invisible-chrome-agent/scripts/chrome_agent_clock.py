"""Monotonic lease clock with conservative, durable restart reconciliation.

Logical timestamps retain their original epoch for compatibility, but wall time is
never used for elapsed time. Same-boot reload consumes monotonic downtime. Across
boots we cannot know elapsed time without trusting a changeable wall clock: keep
only the bounded remaining durations at the last snapshot, then persist a new
anchor. Legacy files get one conservative capped migration, not repeated renewal.
"""
from pathlib import Path
import subprocess
import sys
import time


def boot_identity():
    if sys.platform == 'linux':
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if sys.platform == 'darwin':
        return subprocess.check_output(['sysctl', '-n', 'kern.bootsessionuuid'], text=True, timeout=2).strip()
    raise RuntimeError('no reliable boot identity for lease clock')


class RuntimeClock:
    def __init__(self):
        try:
            self.boot = boot_identity()
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError('cannot establish lease clock boot identity') from exc
        if not self.boot:
            raise RuntimeError('empty boot identity for lease clock')
        self.origin = time.time()
        self.started = time.monotonic()

    def __call__(self):
        return self.origin + max(0, time.monotonic() - self.started)

    def snapshot(self):
        mono = time.monotonic()
        return dict(boot=self.boot, logical=self.origin + max(0, mono - self.started), monotonic=mono)

    def restore(self, data, config):
        saved = data.get('clock')
        mono = time.monotonic()
        if saved:
            elapsed = mono - saved['monotonic']
            same_boot = saved['boot'] == self.boot and elapsed >= 0
            self.origin = saved['logical'] + (elapsed if same_boot else 0)
            self.started = mono
            # Changed settings or a reboot cannot turn saved data into an
            # unbounded future deadline. Same-boot normal deadlines stay exact.
            if not same_boot:
                self._clamp(data, config, legacy=False)
            return not same_boot
        self._clamp(data, config, legacy=True)
        return True

    def _clamp(self, data, config, legacy):
        now = self()
        def deadline(value, bound):
            return now + (bound if legacy else min(bound, max(0, value - now)))
        for lease in data['leases'].values():
            lease['activity'] = now if legacy else min(now, lease['activity'])
            if lease['released'] is not None:
                lease['released'] = now if legacy else min(now, lease['released'])
            lease['requests'] = {key: deadline(value, 86400 + config.request_grace_s)
                                 for key, value in lease['requests'].items()}
            if 'restart_until' in lease:
                lease['restart_until'] = deadline(lease['restart_until'], config.orphan_s)
        for target in data['targets'].values():
            for kind, protection in target['protections'].items():
                bound = config.handoff_s if kind == 'handoff' else config.download_stall_s
                protection['until'] = deadline(protection['until'], bound)
                if 'max_until' in protection:
                    protection['max_until'] = deadline(protection['max_until'], config.download_max_s)
                    protection['until'] = min(protection['until'], protection['max_until'])
        for download in data.get('downloads', {}).values():
            download['max_until'] = deadline(download['max_until'], config.download_max_s)
            download['until'] = min(deadline(download['until'], config.download_stall_s), download['max_until'])

    def from_wall(self, until, maximum):
        return self() + min(maximum, max(0, until - time.time()))
