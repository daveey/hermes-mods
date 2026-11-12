#!/usr/bin/env python3
"""Summarise filtered wall-stack occupancy per thread family and native chain.

Usage: sample <pid> 30 5 -file s.txt && python3 sample_oncpu.py s.txt
Samples with known wait leaves (WAIT below) are excluded heuristically. Retained samples are not
measured CPU time or cores: file I/O and unknown leaves can still block. The existing `read` filter
treats pipe/socket reads as waits; `pread` remains visible. Interpreter dispatch frames are skipped so
the C functions that do the work (sqlite3, json, os.stat, scandir, gc) stay visible.
"""
import collections
import re
import sys

WAIT = {
    "__psynch_cvwait", "read", "waitid", "__semwait_signal", "kevent", "kevent64", "kevent_id",
    "__workq_kernreturn", "__wait4", "poll", "__psynch_mutexwait", "mach_msg2_trap", "__select",
    "__select_nocancel", "recvfrom", "__recvfrom", "accept", "__accept", "__sigsuspend",
    "__ulock_wait", "__ulock_wait2", "__read_nocancel", "__recvmsg", "__psynch_rw_rdlock",
    # XNU mach/syscall_sw.h: these traps wait, optionally with a timeout or signal.
    # Retain signal-only traps; do not filter all semaphore/syscall names.
    "semaphore_wait_trap", "semaphore_wait_signal_trap",
    "semaphore_timedwait_trap", "semaphore_timedwait_signal_trap",
}
SKIP = re.compile(r"^(_TAIL_CALL_|_PyEval_|PyEval_|_PyObject_Vectorcall|PyObject_Vectorcall|_PyFunction_Vectorcall"
                  r"|_PyObject_Call|PyObject_Call|method_vectorcall|cfunction_|vectorcall_|slot_tp_|_Py_|"
                  r"thread_run|pythread_wrapper|_pthread_start|thread_start|context_run|task_step|_PyVectorcall)")


def family(name: str) -> str:
    f = re.sub(r"^Thread_\d+:?\s*", "", name)
    f = re.sub(r"(kanban-feed-).*", r"\1*", f)
    return re.sub(r"_\d+$", "_N", f)


path = sys.argv[1]
lines = open(path, errors="replace").read().split("\n")
start = next(i for i, l in enumerate(lines) if l.startswith("Call graph:"))
end = next(i for i, l in enumerate(lines) if i > start and l.startswith("Total number in stack"))
thr_re = re.compile(r"^    (\d+) (Thread_\d+.*)$")
fr_re = re.compile(r"^(\s{4}[+!:| ]*?)(\d+) (\S.*?)  \(in ([^)]+)\)")

threads = []
for l in lines[start + 1:end]:
    m = thr_re.match(l)
    if m:
        threads.append({"name": m.group(2).strip(), "total": int(m.group(1)), "nodes": []})
        continue
    m = fr_re.match(l)
    if m and threads:
        # [depth, count, function, library, parent index, children's count]
        threads[-1]["nodes"].append([len(m.group(1)), int(m.group(2)), m.group(3).strip(), m.group(4), -1, 0])

ticks = max(t["total"] for t in threads)
per_thread = []
chains_all = collections.Counter()
for t in threads:
    nodes = t["nodes"]
    stack = []
    for i, n in enumerate(nodes):
        while stack and nodes[stack[-1]][0] >= n[0]:
            stack.pop()
        if stack:
            n[4] = stack[-1]
            nodes[stack[-1]][5] += n[1]
        stack.append(i)
    oncpu = 0
    chains = collections.Counter()
    for n in nodes:
        self_ = n[1] - n[5]
        if self_ <= 0 or n[2] in WAIT:
            continue
        oncpu += self_
        names, j = [n[2]], n[4]
        while j >= 0 and len(names) < 6:
            nm = nodes[j][2]
            if not SKIP.match(nm) and nm != names[-1]:
                names.append(nm)
            j = nodes[j][4]
        chains[" < ".join(names)] += self_
    for c, k in chains.items():
        chains_all[(family(t["name"]), c)] += k
    per_thread.append((oncpu, t["name"]))

total_on = sum(p[0] for p in per_thread)
print(f"threads={len(threads)} max samples/thread={ticks} retained stack samples={total_on}")
print("Filtered wall-stack occupancy; not measured CPU time or cores.")
fams = collections.Counter()
for oncpu, name in per_thread:
    fams[family(name)] += oncpu
print("== thread family (share of retained stack samples):")
for f, k in fams.most_common(14):
    print(f"  {k:6d} {100 * k / max(total_on, 1):5.1f}%  {f or '(unnamed)'}")
print("== retained native chains (leaf < callers, interpreter frames skipped):")
for (f, c), k in chains_all.most_common(22):
    print(f"  {k:5d} [{f[:28]}] {c[:190]}")
