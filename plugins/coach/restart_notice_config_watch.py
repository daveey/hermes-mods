#!/usr/bin/env python3
"""Flag Hermes profiles whose home channel would get gateway restart/online notices.

A gateway posts "Hermes is restarting" / "Gateway online" to each platform's home channel unless
`platforms.<platform>.gateway_restart_notification: false`. On a bot whose home channel is a team channel that is
noise for everyone there (alpha-askeve posted 26 notices in 7 h to the Ask Eve channel before the opt-out was added).
This scans `<HERMES_HOME>/config.yaml` (profile `default`) and `<HERMES_HOME>/profiles/*/config.yaml`: for every
platform with a home channel (`platforms.<p>.home_channel.chat_id` in config.yaml, or `<P>_HOME_CHANNEL` in the
profile's `.env`) whose `gateway_restart_notification` is not false, it files ONE Kanban card per (host, profile,
platform), unless allow-listed.

Allow-list: `<HERMES_HOME>/restart-notice-allow.txt`, one `profile` or `profile:platform` per line (`#` comments),
for a home channel only its owner reads (a DM) where restart notices are wanted.

    restart_notice_config_watch.py [--dry-run] [--json] [--home PATH]

--dry-run prints the findings JSON and creates nothing. Exit 0 unless the scan itself crashes.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

ALLOW_FILE = "restart-notice-allow.txt"
ENV_SUFFIX = "_HOME_CHANNEL"
FALSE_TOKENS = {"false", "no", "off", "0"}


def hermes_home(home: Optional[str] = None) -> Path:
    return Path(home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _load_yaml(path: Path):
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        from ruamel.yaml import YAML  # Hermes venv ships ruamel, not PyYAML
        return YAML(typ="safe", pure=True).load(text)


def _read_env(path: Path) -> dict:
    out = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        out[k] = v.strip().strip("'\"")
    return out


def _is_false(value) -> bool:
    """Same reading as core's _coerce_bool(default=True): only an explicit false-ish value opts out."""
    if isinstance(value, bool):
        return value is False
    if isinstance(value, (int, float)):
        return value == 0
    return isinstance(value, str) and value.strip().lower() in FALSE_TOKENS


def _restart_flag(pcfg: dict):
    """Core reads the platform's top-level key, falling back to `extra` (gateway/config.py toplevel_or_extra)."""
    v = pcfg.get("gateway_restart_notification")
    if v is None and isinstance(pcfg.get("extra"), dict):
        v = pcfg["extra"].get("gateway_restart_notification")
    return v


def load_allow(home: Path) -> set:
    try:
        lines = (home / ALLOW_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {ln.split("#", 1)[0].strip() for ln in lines if ln.split("#", 1)[0].strip()}


def profiles(home: Path) -> list:
    """[(name, dir)] — the root home is profile `default`."""
    out = [("default", home)]
    pdir = home / "profiles"
    if pdir.is_dir():
        out += [(p.name, p) for p in sorted(pdir.iterdir()) if p.is_dir() and (p / "config.yaml").exists()]
    return out


def scan_profile(name: str, pdir: Path) -> tuple:
    """-> (findings, errors) for one profile."""
    findings, errors = [], []
    cfg_path = pdir / "config.yaml"
    try:
        cfg = _load_yaml(cfg_path) if cfg_path.exists() else {}
    except Exception as e:  # a config the runtime can't read either: report it, don't skip silently
        return [], [{"profile": name, "config": str(cfg_path), "error": f"{type(e).__name__}: {e}"[:300]}]
    plats = (cfg or {}).get("platforms") or {}
    plats = plats if isinstance(plats, dict) else {}
    env = _read_env(pdir / ".env")
    homes = {}
    for plat, pcfg in plats.items():
        hc = (pcfg or {}).get("home_channel") if isinstance(pcfg, dict) else None
        if isinstance(hc, dict) and hc.get("chat_id"):
            homes[str(plat)] = {"source": "config.yaml", "chat_id": str(hc["chat_id"]), "name": hc.get("name")}
    for k, v in env.items():
        if k.endswith(ENV_SUFFIX) and v:
            plat = k[: -len(ENV_SUFFIX)].lower()
            homes.setdefault(plat, {"source": ".env", "chat_id": v, "name": env.get(k + "_NAME")})
    for plat, h in sorted(homes.items()):
        pcfg = plats.get(plat)
        flag = _restart_flag(pcfg if isinstance(pcfg, dict) else {})
        if not _is_false(flag):
            findings.append({"profile": name, "platform": plat, "home_channel": h["chat_id"],
                             "home_channel_name": h["name"], "home_source": h["source"],
                             "gateway_restart_notification": flag, "config": str(cfg_path)})
    return findings, errors


def scan(home: Optional[str] = None) -> dict:
    root = hermes_home(home)
    allow = load_allow(root)
    findings, allowed, errors, checked = [], [], [], []
    for name, pdir in profiles(root):
        checked.append(name)
        f, e = scan_profile(name, pdir)
        errors += e
        for x in f:
            (allowed if name in allow or f"{name}:{x['platform']}" in allow else findings).append(x)
    return {"profiles_checked": checked, "findings": findings, "allowed": allowed, "errors": errors}


def card_key(host: str, f: dict) -> str:
    return f"restart-notice-config:{host}:{f['profile']}:{f['platform']}"


def card_body(host: str, f: dict) -> str:
    p = "" if f["profile"] == "default" else f"-p {f['profile']} "
    return f"""Detected by restart_notice_config_watch.py (hermes-mods plugins/coach) on {host}.

## Evidence
- profile `{f['profile']}`, platform `{f['platform']}`
- home channel `{f['home_channel']}`{f" ({f['home_channel_name']})" if f['home_channel_name'] else ''}, set in {f['home_source']}
- `platforms.{f['platform']}.gateway_restart_notification` = `{f['gateway_restart_notification']!r}` (unset means true) in `{f['config']}`

## What it means
Every gateway restart (auto-updates restart it often) posts "Hermes is restarting" / "Gateway online" to that home
channel. On a channel other people read that is noise (alpha-askeve: 26 notices in 7 h to a team channel).

## Fix
1. If other people read that channel: `hermes {p}config set platforms.{f['platform']}.gateway_restart_notification false`,
   then read it back with `hermes {p}config get platforms.{f['platform']}.gateway_restart_notification`.
   The running gateway re-reads the flag at shutdown, so no restart is needed.
2. If it is the owner's own DM and the notices are wanted: add `{f['profile']}:{f['platform']}` to
   `~/.hermes/{ALLOW_FILE}` on {host}.
3. Rerun `python3 ~/code/hermes-mods/plugins/coach/restart_notice_config_watch.py --dry-run` and confirm it no longer
   lists this profile.
"""


def create_argv(host: str, f: dict) -> list:
    return ["hermes", "kanban", "create", "--assignee", "default", "--idempotency-key", card_key(host, f),
            "--created-by", "restart-notice-config-watch", "--body-file", "-", "--json",
            f"Profile {f['profile']}: {f['platform']} home channel gets gateway restart notices "
            f"(gateway_restart_notification not false)"]


def file_cards(findings: list, host: str, runner: Optional[Callable] = None) -> list:
    """One `hermes kanban create` per finding; the idempotency key returns the existing card for a repeat."""
    run = runner or subprocess.run
    out = []
    for f in findings:
        r = run(create_argv(host, f), input=card_body(host, f), capture_output=True, text=True, timeout=120)
        out.append({"key": card_key(host, f), "rc": r.returncode, "out": ((r.stdout or "") + (r.stderr or ""))[-300:]})
    return out


def main(argv=None, runner: Optional[Callable] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print findings JSON, create no card")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--home", help="HERMES_HOME to scan (default $HERMES_HOME or ~/.hermes)")
    a = ap.parse_args(argv)
    host = socket.gethostname().split(".")[0]
    res = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": host,
           "home": str(hermes_home(a.home)), "dry_run": a.dry_run, **scan(a.home), "cards": []}
    if res["findings"] and not a.dry_run:
        res["cards"] = file_cards(res["findings"], host, runner)
    if a.json or a.dry_run:
        print(json.dumps(res))
    elif res["findings"] or res["errors"]:  # silent when clean: the cron wrapper delivers stdout
        lines = [f"{res['ts']} {host}: {len(res['findings'])} profile home channel(s) get restart notices"]
        lines += [f"  {f['profile']}:{f['platform']} -> {c['out'].strip()[-80:]}"
                  for f, c in zip(res["findings"], res["cards"])]
        lines += [f"  unreadable {e['config']}: {e['error']}" for e in res["errors"]]
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
