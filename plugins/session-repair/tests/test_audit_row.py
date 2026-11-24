from sr_helpers import load, PLUGIN
run=load('_audit_row_run',PLUGIN/'run.py')

def test_action_ledger_preserves_scan_evidence_for_gate():
    finding={'id':'BS-ready-worker-free','fingerprint':'BS-ready-worker-free:default/t_example',
             'evidence':{'eligible_worker':{'capacity':3},'last_skip':None},'fix':{'kind':'card'}}
    assert run._row(123,'shadow',finding)['evidence']==finding['evidence']
