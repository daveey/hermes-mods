"""Synthetic native stacks only: no captured process data is needed."""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sample_oncpu.py"


def run_sample(tmp_path, graph):
    sample = tmp_path / "synthetic.txt"
    sample.write_text("Call graph:\n" + graph + "Total number in stack (recursive counted multiple, when >=5):\n")
    result = subprocess.run([sys.executable, str(SCRIPT), str(sample)],
                            text=True, capture_output=True, check=True)
    return result.stdout


def test_caulk_waits_excluded_sqlite_work_retained(tmp_path):
    output = run_sample(tmp_path, """    100 Thread_1: caulk.messenger.shared
    + 100 semaphore_wait_trap  (in libsystem_kernel.dylib)
    100 Thread_2: caulk.messenger.shared
    + 100 semaphore_wait_trap  (in libsystem_kernel.dylib)
    100 Thread_3: database
    + 100 _pthread_start  (in libsystem_pthread.dylib)
    +   100 sqlite3_step  (in libsqlite3.dylib)
    +     60 sqlite3BtreeMovetoUnpacked  (in libsqlite3.dylib)
    +       60 pread  (in libsystem_kernel.dylib)
    +     40 __psynch_cvwait  (in libsystem_kernel.dylib)
""")
    assert "semaphore_wait_trap" not in output
    assert "retained stack samples=60" in output
    assert "pread < sqlite3BtreeMovetoUnpacked < sqlite3_step" in output
    assert "100.0%  database" in output
    assert "0.0%  caulk.messenger.shared" in output
    assert "not measured CPU time or cores" in output
    assert "cores busy" not in output
    assert "on-CPU samples" not in output


@pytest.mark.parametrize("leaf", [
    "semaphore_wait_trap", "semaphore_wait_signal_trap",
    "semaphore_timedwait_trap", "semaphore_timedwait_signal_trap",
    "__psynch_cvwait", "mach_msg2_trap",
])
def test_wait_only_has_zero_retained_samples(tmp_path, leaf):
    output = run_sample(tmp_path, f"    10 Thread_1: waiter\n    + 10 {leaf}  (in libsystem_kernel.dylib)\n")
    assert "retained stack samples=0" in output
    assert leaf not in output


@pytest.mark.parametrize("leaf", [
    "pread", "pwrite", "stat", "semaphore_signal_trap",
    "semaphore_signal_all_trap", "semaphore_signal_thread_trap",
    "sqlite3_step", "unknown_trap",
])
def test_work_and_non_wait_syscalls_not_blanket_excluded(tmp_path, leaf):
    output = run_sample(tmp_path, f"    10 Thread_1: worker\n    + 10 {leaf}  (in example)\n")
    assert "retained stack samples=10" in output
    assert leaf in output
