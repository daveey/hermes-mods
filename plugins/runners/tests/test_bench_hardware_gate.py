"""The manual hardware command must not confuse skips with evidence."""
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

spec = importlib.util.spec_from_file_location('hardware_gate', Path(__file__).resolve().parents[1] / 'scripts/check_inventory_hardware.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.mark.parametrize('fault', [None, 'skip', 'fail', 'missing', 'duplicate'])
def test_report_requires_both_executed_patterns(tmp_path, fault):
    root = ET.Element('testsuite')
    names = sorted(gate.EXPECTED)
    if fault == 'missing':
        names.pop()
    if fault == 'duplicate':
        names[1] = names[0]
    for name in names:
        case = ET.SubElement(root, 'testcase', name=name)
    if fault in ('skip', 'fail'):
        ET.SubElement(case, 'skipped' if fault == 'skip' else 'failure')
    report = tmp_path / 'report.xml'
    ET.ElementTree(root).write(report)
    if fault is None:
        gate.verify_report(report)
    else:
        with pytest.raises(ValueError):
            gate.verify_report(report)


@pytest.mark.parametrize('fault', ['sha', 'dirty', 'cardinality', 'output'])
def test_command_rejects_invalid_inputs(tmp_path, monkeypatch, fault):
    sha = 'a' * 40
    def git(*args):
        if args[0] == 'rev-parse':
            return 'b' * 40 if fault == 'sha' else sha
        return ' M changed' if fault == 'dirty' else ''
    monkeypatch.setattr(gate, 'git', git)
    monkeypatch.setenv('RUNNERS_BENCH_RUNNERS', '1' if fault == 'cardinality' else '1900')
    output = gate.ROOT / 'invalid-evidence' if fault == 'output' else tmp_path / 'evidence'
    monkeypatch.setattr('sys.argv', ['gate', '--head', sha, '--output', str(output)])
    with pytest.raises(SystemExit) as result:
        gate.main()
    assert result.value.code == 2
    assert not output.exists()
