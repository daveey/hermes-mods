"""Passive, profile-mounted person dependency read model (no mutation authority)."""
import importlib.util
from pathlib import Path
from fastapi import APIRouter, HTTPException

_spec = importlib.util.spec_from_file_location('_outcome_people_api', Path(__file__).resolve().parents[1] / 'person_waits.py')
assert _spec and _spec.loader
people = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(people)


def create_router():
    router = APIRouter()

    @router.get('/person-waits')
    def person_waits(ids: str):
        sessions = list(dict.fromkeys(ids.split(',')))
        if len(sessions) > 32 or any(not s or len(s) > 256 for s in sessions):
            raise HTTPException(400, 'supply 1..32 session IDs')
        try:
            roots = list(dict.fromkeys(people.root(sid) for sid in sessions))
            return {'records': [{**r, 'session': sid} for sid in roots for r in people.open_records(sid)]}
        except Exception as exc:
            raise HTTPException(503, 'person dependency ledger unreadable') from exc

    return router


# Core's dashboard loader imports this attribute (it does not call factories).
router = create_router()
