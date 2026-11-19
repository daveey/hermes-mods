#!/usr/bin/env python3
"""Allocator split of a live macOS process from `vmmap --summary <pid>`.

Read-only, but vmmap can suspend the task. Use only an authorized diagnostic target.

usage: alloc_split.py <pid> [<pid> ...]

Prints, in MiB: physical footprint, Untagged dirty + swapped bytes, malloc
bytes in use, and a free-memory estimate (zone fragmentation plus empty-region
dirty + swapped bytes). Untagged is not uniquely attributable to pymalloc;
the free estimate is not an exact decomposition of physical footprint.
"""
import re
import subprocess
import sys

UNIT = {'K': 1 / 1024, 'M': 1.0, 'G': 1024.0, 'B': 1 / 1048576}


def mb(tok: str) -> float:
    m = re.fullmatch(r'([\d.]+)([KMGB])', tok)
    if not m:
        raise ValueError(f"unsupported vmmap size: {tok!r}")
    return float(m.group(1)) * UNIT[m.group(2)]


def split(pid: str) -> str:
    if not re.fullmatch(r'[1-9][0-9]*', pid):
        raise ValueError('pid must be a positive integer')
    out = subprocess.run(['vmmap', '--summary', pid], capture_output=True, text=True,
                         check=True, timeout=30).stdout
    foot = re.search(r'^Physical footprint:\s+(\S+)', out, re.M)
    pymalloc = empty = 0.0
    for line in out.splitlines():
        # columns: VIRTUAL RESIDENT DIRTY SWAPPED ...
        m = re.match(r'^(Untagged|Malloc \w+ \(empty\))\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)', line)
        if not m:
            continue
        counted = mb(m.group(4)) + mb(m.group(5))
        if m.group(1) == 'Untagged':
            pymalloc += counted
        else:
            empty += counted
    # zone table TOTAL: VIRTUAL RESIDENT DIRTY SWAPPED COUNT ALLOCATED FRAG %FRAG REGIONS
    zone = re.search(r'^TOTAL\s+\S+\s+\S+\s+\S+\s+\S+\s+(\d+)\s+(\S+)\s+(\S+)\s+\d+%', out, re.M)
    if not foot or not zone:
        raise ValueError('vmmap output missing footprint or malloc zone totals')
    blocks, used, frag = int(zone.group(1)), mb(zone.group(2)), mb(zone.group(3))
    return (f"pid {pid}: footprint {mb(foot.group(1)) if foot else 0:.0f} MB | untagged dirty+swapped {pymalloc:.0f} | "
            f"malloc in use {used:.0f} ({blocks} blocks) | malloc free estimate {frag + empty:.0f} "
            f"(fragmentation {frag:.0f} + empty regions {empty:.0f})")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('usage: alloc_split.py <pid> [<pid> ...]')
    try:
        for p in sys.argv[1:]:
            print(split(p))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        sys.exit(f'alloc_split: {exc}')
