"""Bounded, read-only GitHub producer reads. Never interpret a ref as shell text."""
import json
import re
import subprocess

REF = re.compile(r'https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/(actions/runs|pull)/(\d+)(?:[/?#].*)?\Z')

def read_github(kind, ref, runner=subprocess.run):
    m = REF.fullmatch(ref) if kind in ('ci', 'deploy', 'external') else None
    if not m:
        return {'state': 'not_resolvable', 'happened': False}
    repo, what, number = m.groups()
    args = (['run','view',number,'--repo',repo,'--json','status,conclusion,url'] if what=='actions/runs'
            else ['pr','view',number,'--repo',repo,'--json','state,reviewDecision,statusCheckRollup,url'])
    try:
        r = runner(['token-broker','exec','--scope','user/github','--reason','session-repair producer readback','--','gh',*args],
                   capture_output=True, text=True, timeout=25)
        if r.returncode:
            return {'state':'readback_failed','happened':False, 'rc':r.returncode}
        data = json.loads(r.stdout)
        if not isinstance(data, dict):
            return {'state':'readback_failed','happened':False}
        if what=='actions/runs':
            happened = data.get('status')=='completed'
        else:
            checks = data.get('statusCheckRollup') or []
            green = bool(checks) and all((x.get('conclusion')=='SUCCESS' or x.get('state')=='SUCCESS') for x in checks)
            happened = data.get('state') in ('MERGED','CLOSED') or data.get('reviewDecision')=='APPROVED' or green
        return {'state':data,'happened':happened}
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return {'state':'readback_failed','happened':False}
