"""Home-local identity and origin projection for explicit-home panel reads.

Never consult process HERMES_HOME, kanban_home, sync_config, load_identity, or
get_install_id. Installation identity is either a server-trusted mapping or
files under the authorized home.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


_INSTALL_ID = re.compile(r'^[0-9a-f]{32}$')


def profile_label(home: Path) -> str:
    home = Path(home)
    return home.name if home.parent.name == 'profiles' else 'default'


def read_install_id(home: Path) -> str | None:
    path = Path(home) / 'install_id'
    try:
        text = path.read_text(encoding='utf-8').strip().lower()
    except OSError:
        return None
    return text if _INSTALL_ID.fullmatch(text) else None


def parse_home_sync_config(home: Path) -> dict:
    path = Path(home) / 'config.yaml'
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return {}
    data = None
    try:
        data = json.loads(text)
    except ValueError:
        try:
            from ruamel.yaml import YAML
            data = YAML(typ='safe').load(text)
        except Exception:
            return {}
    if not isinstance(data, dict):
        return {}
    kanban = data.get('kanban')
    if not isinstance(kanban, dict):
        return {}
    sync = kanban.get('sync')
    return sync if isinstance(sync, dict) else {}


def identity(home: Path, installation=None) -> dict:
    inst = installation if isinstance(installation, dict) else {}
    home = Path(home)
    gateway = inst.get('gateway')
    profile = inst.get('profile')
    install_id = inst.get('install_id')
    if profile is None:
        profile = profile_label(home)
    if gateway is None:
        raw = parse_home_sync_config(home).get('gateway')
        gateway = raw.strip() if isinstance(raw, str) and raw.strip() else None
    if install_id is None:
        install_id = read_install_id(home)
    elif isinstance(install_id, str):
        install_id = install_id.strip().lower()
        if not _INSTALL_ID.fullmatch(install_id):
            install_id = None
    return {'gateway': gateway, 'profile': profile, 'install_id': install_id}


def origin_rows(home: Path, *, gateway: str, profile: str) -> list:
    from hermes_cli.kanban_origin_ledger import project
    path = Path(home) / 'kanban' / 'origin-ledger.db'
    return project(path, replica=None, gateway=gateway or '', profile=profile or 'default')
