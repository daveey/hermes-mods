#!/usr/bin/env python3
"""Trusted, manual prelanding gate. Never execute unreviewed PR code automatically.

Run on deployment-class hardware with --head FULL_SHA --output OUTSIDE_CHECKOUT.
Writes exact-head evidence for independent review; NOT a signed CI attestation.
The operator/reviewer must establish hardware identity. No host-name allowlist can
prove hardware speed. Both compatibility interpreters and both patterns must pass.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
EXPECTED = {'test_inventory_replay_absolute_budget[clustered]',
            'test_inventory_replay_absolute_budget[scattered]'}


def verify_report(path):
    cases = list(ET.parse(path).iter('testcase'))
    if len(cases) != 2 or {c.attrib['name'] for c in cases} != EXPECTED:
        raise ValueError('missing or unexpected absolute replay cases')
    if any(list(c) for c in cases):
        raise ValueError('absolute replay skipped, failed or emitted unexpected case data')


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--head', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{40}', args.head) or git('rev-parse', 'HEAD') != args.head:
        parser.error('exact 40-character checkout SHA required')
    if git('status', '--porcelain'):
        parser.error('clean committed checkout required')
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error('evidence output must be outside checkout')
    if os.environ.get('RUNNERS_BENCH_RUNNERS', '1900') != '1900':
        parser.error('absolute acceptance requires 1900 runners')
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, RUNNERS_BENCH_REQUIRE_ABSOLUTE='1', RUNNERS_BENCH_RUNNERS='1900')
    for key in ('PYTEST_ADDOPTS', 'PYTHONPATH', 'PYTHONHOME'):
        env.pop(key, None)
    receipt = dict(head=args.head, host=platform.node(), platform=platform.platform(),
                   runners=1900, span=600, budget_core_percent=0.1, results=[])
    try:
        for version in ('3.11', '3.14'):
            command = ['uv', 'run', '--python', version, '--with', 'pytest', '--with', 'pyyaml',
                       '--with', 'msgspec>=0.19,<1', '--with', 'jsonschema', 'python']
            # Import provenance and real interpreter version are recorded before pytest.
            probe = subprocess.check_output(command + ['-c',
                'import sys, pathlib, plugins.runners.service as s; '
                'print(sys.version); print(pathlib.Path(s.__file__).resolve()); '
                f'assert sys.version_info[:2] == {tuple(map(int, version.split(".")))}; '
                f'assert pathlib.Path(s.__file__).resolve() == pathlib.Path({str(ROOT / "plugins/runners/service.py")!r})'],
                cwd=ROOT, env=env, text=True)
            report = output / f'{version}.xml'
            with (output / f'{version}.log').open('w') as log:
                log.write(probe); log.flush()
                result = subprocess.run(command + ['-m', 'pytest', '-s', '-q', '-p', 'no:cacheprovider',
                    '--override-ini=addopts=', '--junitxml', str(report),
                    'plugins/runners/tests/test_bench_inventory.py::test_inventory_replay_absolute_budget'],
                    cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
            if result.returncode:
                raise RuntimeError(f'{version} replay failed: exit {result.returncode}')
            verify_report(report)
            receipt['results'].append(dict(python=version, cases=sorted(EXPECTED), passed=True, provenance=probe))
        if git('rev-parse', 'HEAD') != args.head or git('status', '--porcelain'):
            raise RuntimeError('checkout changed during proof')
        receipt['passed'] = True
    finally:
        (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
