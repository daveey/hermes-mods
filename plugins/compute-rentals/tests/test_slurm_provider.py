import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CFG = dict(hosts=['node1', 'node4'], user='ops', control_host='node1')


def test_slurm_physical_nodes_dedup_prepaid_and_sanitized():
    from providers.slurm import SlurmCollector
    def read(host, command):
        if 'show config' in command:
            return 'ClusterName = cluster-a\nSecret = OMIT_ME'
        if 'show nodes' in command:
            return ('NodeName=node1 CPUTot=32 RealMemory=128000 Gres=gpu:4090:1 State=ALLOCATED Partitions=a Secret=OMIT_ME\n'
                    'NodeName=node1 CPUTot=32 RealMemory=128000 Gres=gpu:4090:1 State=ALLOCATED Partitions=b\n')
        return ''
    out = SlurmCollector(read=read).collect()
    assert out['status'] == 'ok'
    assert len(out['machines']) == 1
    node = out['machines'][0]
    assert node['resource_kind'] == 'physical_node'
    assert node['billing_class'] == 'internal_prepaid'
    assert node['currency'] is None and node['hourly_rate'] is None
    assert node['expires_at'] is None
    assert 'not GPU occupancy' in node['state_detail']
    assert 'OMIT_ME' not in str(out)
    assert out['workloads'] == []


def test_slurm_arrays_multinode_and_deadlines_not_machines():
    from providers.slurm import SlurmCollector
    from inventory import normalize_machine
    def read(host, command):
        if 'show config' in command: return 'ClusterName = cluster-a'
        if 'show nodes' in command:
            return 'NodeName=node1 State=IDLE\nNodeName=node4 State=DOWN'
        return ('123_1|RUNNING|node[1,4]|8|gres/gpu:2|2026-10-09T20:00:00|2026-10-10T20:00:00\n'
                '123_2|PENDING|(null)|8|gres/gpu:2|N/A|UNLIMITED\n')
    out = SlurmCollector(read=read).collect()
    assert len(out['machines']) == 2
    jobs = out['workloads']
    assert len(jobs) == 2
    assert jobs[0]['provider_id'] == '123_1'
    assert jobs[0]['parent_machine_keys'] == [normalize_machine(n, 'slurm')['key'] for n in out['machines']]
    assert jobs[0]['expires_at'] == '2026-10-10T20:00:00+00:00'
    assert jobs[1]['resource_kind'] == 'queued_job'
    assert jobs[1]['parent_machine_keys'] == []
    assert jobs[1]['expires_at'] is None


def test_slurm_failed_job_read_preserves_nodes_without_raw_error():
    from providers.slurm import SlurmCollector
    def read(host, command):
        if 'show config' in command: return 'ClusterName = cluster-a'
        if 'show nodes' in command: return 'NodeName=node1 State=IDLE'
        raise RuntimeError('SECRET')
    out = SlurmCollector(read=read).collect()
    assert out['status'] == 'partial'
    assert len(out['machines']) == 1
    assert 'SECRET' not in str(out)


def test_hostlist_bounds_and_transport_reject_execution():
    import pytest
    from providers.slurm import expand_hosts, ssh_read
    assert expand_hosts('node[01-02],gpu[1,4]') == ['node01', 'node02', 'gpu1', 'gpu4']
    for bad in ('node[1-9999999]', 'node1; touch secret', 'node[2-1]'):
        with pytest.raises(ValueError): expand_hosts(bad)
    for host, cmd in [('untrusted', 'TZ=UTC scontrol show config'), ('node1', 'srun true'), ('node1', 'docker exec app true')]:
        with pytest.raises(ValueError): ssh_read(host, cmd, config=CFG)


def test_transport_time_byte_caps_and_safe_ssh_options(monkeypatch):
    import subprocess
    import pytest
    from providers import slurm
    real = subprocess.Popen
    calls = []
    def start(script):
        def popen(argv, **kwargs):
            calls.append(argv)
            return real([sys.executable, '-c', script], **kwargs)
        monkeypatch.setattr(slurm.subprocess, 'Popen', popen)
    start("print('hello')")
    assert slurm.ssh_read('node1', 'TZ=UTC scontrol show config', config=CFG) == 'hello\n'
    assert 'BatchMode=yes' in calls[0] and 'StrictHostKeyChecking=yes' in calls[0]
    assert calls[0][-2] == 'ops@node1'
    start("print('x'*1024)")
    with pytest.raises(RuntimeError, match='byte cap'):
        slurm.ssh_read('node1', 'TZ=UTC scontrol show config', byte_limit=10, config=CFG)
    start('import time; time.sleep(1)')
    with pytest.raises(RuntimeError, match='timeout'):
        slurm.ssh_read('node1', 'TZ=UTC scontrol show config', timeout=.01, config=CFG)


def test_slurm_row_caps_and_malformed_rows_preserve_coverage():
    from providers.slurm import SlurmCollector
    def read(host, command):
        if 'show config' in command: return 'ClusterName = cluster-a'
        if 'show nodes' in command: return 'NodeName=node1 State=IDLE\nNodeName=node4 State=IDLE'
        return ''
    out = SlurmCollector(read=read, max_rows=1).collect()
    assert out['status'] == 'partial'
    assert len(out['machines']) == 1




def test_unconfigured_slurm_reports_unconfigured_without_ssh(monkeypatch):
    from providers import slurm
    def boom(*a, **k): raise AssertionError('no ssh when unconfigured')
    monkeypatch.setattr(slurm.subprocess, 'Popen', boom)
    out = slurm.SlurmCollector(config=dict(hosts=[], user=None, control_host=None)).collect()
    assert out['status'] == 'unconfigured'
    assert out['machines'] == [] and out['workloads'] == []


def test_control_host_and_extra_commands_are_configurable():
    import pytest
    from providers.slurm import SlurmCollector, ssh_read
    seen = []
    def read(host, command):
        seen.append(host)
        return 'ClusterName = c' if 'show config' in command else ''
    SlurmCollector(read=read, config=dict(hosts=['a', 'b'], user=None, control_host='b')).collect()
    assert set(seen) == {'b'}
    with pytest.raises(ValueError): ssh_read('node1', 'uptime', config=CFG)
