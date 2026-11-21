"""Session-repair findings API, mounted at /api/plugins/session-repair (read-only; see ../README.md).

GET /findings: the latest scan the cron tick wrote (`$HERMES_HOME/session-repair/last.json`), with `age_s`;
`?fresh=1` runs the read-only scanner now instead (nothing is stored, deduped or handed over). `?chat=<id>` keeps
only that chat's findings (for a future session-panel pip).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

PLUGIN_ID = 'session-repair'
ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    """A sibling module by path, reloaded when its file changes."""
    key = f'_session_repair_{name}'
    mtime = path.stat().st_mtime_ns
    mod = sys.modules.get(key)
    if mod is not None and getattr(mod, '_loaded_mtime', None) == mtime:
        return mod
    spec = importlib.util.spec_from_file_location(key, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    mod._loaded_mtime = mtime
    return mod


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001
        return Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')


def _last() -> dict:
    p = _home() / 'session-repair' / 'last.json'
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return {'findings': [], 'scanned_at': None, 'source': 'none'}
    data['source'] = 'cron'
    return data


def _only(data: dict, chat: Optional[str]) -> dict:
    if chat:
        data = {**data, 'findings': [f for f in data.get('findings') or [] if f.get('chat') == chat]}
    at = data.get('scanned_at')
    data['age_s'] = round(time.time() - at, 1) if isinstance(at, (int, float)) else None
    return data


router = APIRouter()


@router.get('/findings')
async def findings(fresh: bool = False, chat: Optional[str] = None):
    if fresh:
        scan = _load('scan', ROOT / 'scan.py')
        data = await run_in_threadpool(scan.scan)
        data['source'] = 'fresh'
    else:
        data = _last()
    return _only(data, chat)
