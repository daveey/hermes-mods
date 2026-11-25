"""Bounded, read-only GitHub producer reads. Never interpret a ref as shell text."""
import datetime
import math
import json
import re
import subprocess

REF = re.compile(r'https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/(actions/runs|pull)/(\d+)(?:[/?#].*)?\Z')

def read_github(kind, ref, runner=subprocess.run):
    m = REF.fullmatch(ref) if kind in ('ci', 'deploy', 'external') else None
    if not m:
        return {'state': 'not_resolvable', 'happened': False}
    repo, what, number = m.groups()
    args = (['run','view',number,'--repo',repo,'--json','status,conclusion,url,updatedAt,headSha'] if what=='actions/runs'
            else ['pr','view',number,'--repo',repo,'--json','state,reviewDecision,statusCheckRollup,url,updatedAt,headRefOid'])
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
            # A review/check result is not a landed PR, and a landed PR is not
            # a deployment. Custom probe predicates are reconciled by the scanner.
            happened = kind != 'deploy' and data.get('state') in ('MERGED','CLOSED')
        happened_at = None
        if happened:
            try:
                stamp = datetime.datetime.fromisoformat(data['updatedAt'].replace('Z', '+00:00'))
                if stamp.tzinfo is not None and math.isfinite(stamp.timestamp()):
                    happened_at = stamp.timestamp()
            except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
                pass  # missing timestamps cannot justify an early owner nudge
        return {'state':data,'happened':happened, 'happened_at':happened_at}
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return {'state':'readback_failed','happened':False}
