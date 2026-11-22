"""Acceptance cannot report green on skipped/missing/failed composed cases."""
import xml.etree.ElementTree as ET
import pytest
from test_withdrawal import ROOT, load_module

results = load_module('composed_results_tests', ROOT / 'composed_results.py')


def reports(directory):
    for stem, names in results.REQUIRED.items():
        root = ET.Element('testsuites')
        suite = ET.SubElement(root, 'testsuite')
        for name in sorted(names):
            ET.SubElement(suite, 'testcase', name=name)
        ET.ElementTree(root).write(directory / (stem + '.xml'))


def test_complete_manifest_passes(tmp_path, capsys):
    reports(tmp_path)
    assert results.verify(tmp_path) == 9
    assert '9 required PASS; 9 total PASS; 0 SKIP' in capsys.readouterr().out


@pytest.mark.parametrize('mutation', ['skipped', 'failure', 'error', 'missing-case',
                                      'missing-report', 'duplicate', 'malformed'])
def test_nonpassing_manifest_fails_closed(tmp_path, mutation):
    reports(tmp_path)
    path = tmp_path / 'test_remote_card_async_custody.xml'
    tree = ET.parse(path)
    suite = tree.getroot().find('testsuite')
    case = suite.find('testcase')
    if mutation in ('skipped', 'failure', 'error'):
        ET.SubElement(case, mutation)
    elif mutation == 'missing-case':
        suite.remove(case)
    elif mutation == 'duplicate':
        ET.SubElement(suite, 'testcase', name=case.attrib['name'])
    tree.write(path)
    if mutation == 'missing-report':
        path.unlink()
    elif mutation == 'malformed':
        path.write_text('not XML')
    with pytest.raises((ValueError, OSError, ET.ParseError)):
        results.verify(tmp_path)
