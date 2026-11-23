#!/usr/bin/env python3
"""Per-thread context-switch and CPU rates for one process (Linux /proc).

Usage: python3 thread_cs.py <pid> [seconds=20]
Prints the process total vol/nonvol switches per second, then the top 15 threads by
voluntary switches with CPU% of one core and thread name (comm). Read-only; never signals.
"""
import os, sys, time

pid = sys.argv[1]
dur = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
hz = os.sysconf('SC_CLK_TCK')


def snap():
    out = {}
    for t in os.listdir(f'/proc/{pid}/task'):
        try:
            st = open(f'/proc/{pid}/task/{t}/status').read()
            v = int(st.split('voluntary_ctxt_switches:')[1].split()[0])
            nv = int(st.split('nonvoluntary_ctxt_switches:')[1].split()[0])
            name = open(f'/proc/{pid}/task/{t}/comm').read().strip()
            f = open(f'/proc/{pid}/task/{t}/stat').read().rsplit(')', 1)[1].split()
            out[t] = (v, nv, int(f[11]) + int(f[12]), name)
        except (OSError, IndexError, ValueError):
            pass  # thread exited mid-read
    return out


a = snap(); time.sleep(dur); b = snap()
rows = [((v - a[t][0]) / dur, (nv - a[t][1]) / dur, (c - a[t][2]) * 100 / hz / dur, t, n)
        for t, (v, nv, c, n) in b.items() if t in a]
rows.sort(reverse=True)
print(f"threads={len(b)} vol/s={sum(r[0] for r in rows):.0f} nonvol/s={sum(r[1] for r in rows):.0f}")
for v, nv, c, t, n in rows[:15]:
    print(f"tid={t:>8} vol/s={v:>8.0f} nonvol/s={nv:>6.0f} cpu%={c:>4.0f} {n}")
