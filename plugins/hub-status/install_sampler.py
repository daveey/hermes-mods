"""Install the optional Desktop-independent Linux user service (stdlib only)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def _quote(value: str, *, command: bool = False) -> str:
    # systemd specifiers expand even inside quotes; ExecStart also expands dollars.
    if any(c in value for c in "\n\r\0"):
        raise ValueError("unit paths cannot contain control characters")
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    return '"' + (value.replace('$', '$$') if command else value) + '"'


def service_text(home: Path, python: Path, plugin: Path) -> str:
    _quote(str(plugin))  # validate control characters; WorkingDirectory is not an argv list
    directory = str(plugin).replace('%', '%%')
    return f'''[Unit]
Description=Hermes hub status transition sampler (independent of Desktop and gateway)


[Service]
Type=simple
WorkingDirectory={directory}
ExecStart={_quote(str(python), command=True)} -m core --home {_quote(str(home), command=True)}
Restart=always
RestartSec=5
Nice=10

[Install]
WantedBy=default.target
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes"))
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    unit = config / "systemd/user/hub-status-sampler.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(service_text(args.home.resolve(), args.python.absolute(), Path(__file__).resolve().parent))
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", unit.name], check=True)
    # enable --now does not replace an already running service after a unit edit.
    subprocess.run(["systemctl", "--user", "restart", unit.name], check=True)
    print(unit)


if __name__ == "__main__":
    main()
