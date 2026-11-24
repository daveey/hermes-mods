"""Explicit-home config and identity inputs; never resolve process-home state."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

_INSTALL_ID = re.compile(r'^[0-9a-f]{32}$')


def profile_label(home: Path) -> str:
    home = Path(home)
    return home.name if home.parent.name == 'profiles' else 'default'


def read_install_id(home: Path) -> str | None:
    path = Path(home) / 'install_id'
    try:
        text = path.read_text(encoding='utf-8-sig').strip().lower()
    except FileNotFoundError:
        return None
    if not _INSTALL_ID.fullmatch(text):
        raise ValueError('Invalid explicit install identity')
    return text


def parse_home_sync_config(home: Path) -> dict:
    path = Path(home) / 'config.yaml'
    try:
        text = path.read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        return {}  # missing configuration disables optional sources; unreadable is not missing
    try:
        data = json.loads(text)
    except ValueError:
        from ruamel.yaml import YAML
        data = YAML(typ='safe').load(text)
    if not isinstance(data, dict):
        raise ValueError('Explicit configuration must be a mapping')
    kanban = data.get('kanban', {})
    if not isinstance(kanban, dict):
        raise ValueError('Explicit kanban configuration must be a mapping')
    sync = kanban.get('sync', {})
    if not isinstance(sync, dict):
        raise ValueError('Explicit sync configuration must be a mapping')
    if 'enabled' in sync and not isinstance(sync['enabled'], bool):
        raise ValueError('Explicit sync enabled must be boolean')
    if 'gateway' in sync and (not isinstance(sync['gateway'], str) or not sync['gateway'].strip()):
        raise ValueError('Explicit sync gateway must be a nonempty string')
    stale_after(sync)  # validate a supplied interval even when the optional source is disabled
    return sync


def stale_after(sync: dict) -> float:
    from hermes_cli.kanban_session_activity import HUB_STALE_AFTER_S
    period = sync.get('interval_seconds', 0)
    if isinstance(period, bool) or not isinstance(period, (int, float)) or not math.isfinite(period) or period < 0:
        raise ValueError('Explicit sync interval_seconds must be finite and nonnegative')
    return max(HUB_STALE_AFTER_S, 3 * period)


def identity(home: Path, installation=None) -> dict:
    if installation is not None and not isinstance(installation, dict):
        raise ValueError('Explicit installation must be a mapping')
    inst = installation or {}
    home = Path(home)
    gateway = inst.get('gateway')
    profile = inst.get('profile', profile_label(home))
    install_id = inst.get('install_id')
    if not isinstance(profile, str) or not profile.strip():
        raise ValueError('Explicit profile must be a nonempty string')
    if gateway is None:
        gateway = parse_home_sync_config(home).get('gateway')
    if gateway is not None and (not isinstance(gateway, str) or not gateway.strip()):
        raise ValueError('Explicit gateway must be a nonempty string')
    if install_id is None:
        install_id = read_install_id(home)
    elif not isinstance(install_id, str) or not _INSTALL_ID.fullmatch(install_id.strip().lower()):
        raise ValueError('Invalid explicit install identity')
    else:
        install_id = install_id.strip().lower()
    return {'gateway': gateway, 'profile': profile, 'install_id': install_id}


def origin_rows(home: Path, *, gateway: str, profile: str) -> list:
    from hermes_cli.kanban_origin_ledger import project
    path = Path(home) / 'kanban' / 'origin-ledger.db'
    return project(path, replica=None, gateway=gateway or '', profile=profile, home=Path(home))
