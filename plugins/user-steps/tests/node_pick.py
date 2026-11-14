"""Pick a Node new enough for the Desktop contract runner (module.register needs >= 18.19).

The first `node` on PATH is not always usable: a host can carry an old system Node ahead of a
current one. Candidates are tried in order: every `node` on PATH, then the Hermes-managed
`~/.hermes/tools/node-*/bin/node` (newest first), then `/opt/homebrew/bin/node`.
"""
import os
from pathlib import Path
import re
import subprocess

MIN_NODE = (18, 19)
HOMEBREW_NODE = Path('/opt/homebrew/bin/node')
_VERSION = re.compile(r'v?(\d+)\.(\d+)(?:\.(\d+))?')


class NodeNotFound(AssertionError):
    """No candidate Node satisfies MIN_NODE; the message lists every candidate tried."""


def _parse(text):
    match = _VERSION.search(text or '')
    return tuple(int(part or 0) for part in match.groups()) if match else None


def node_version(path):
    """Return (parsed version or None, display text) for one node executable."""
    try:
        result = subprocess.run([str(path), '--version'], text=True, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as error:
        return None, f'unusable ({type(error).__name__}: {error})'
    text = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        return None, f'unusable (exit {result.returncode}: {text})'
    version = _parse(text)
    return version, text if version else f'unparseable version {text!r}'


def node_candidates(path_env=None, home=None, homebrew=HOMEBREW_NODE):
    """Existing node executables in preference order, without duplicates."""
    path_env = os.environ.get('PATH', '') if path_env is None else path_env
    home = Path.home() if home is None else Path(home)
    found = [Path(entry) / 'node' for entry in path_env.split(os.pathsep) if entry]
    tools = [
        (_parse(tool.parent.parent.name[len('node-'):]) or (0,), tool)
        for tool in (home / '.hermes' / 'tools').glob('node-*/bin/node')
    ]
    found += [tool for _, tool in sorted(tools, key=lambda item: item[0], reverse=True)]
    found.append(Path(homebrew))
    seen, candidates = set(), []
    for candidate in found:
        key = str(candidate)
        if key in seen or not (candidate.is_file() and os.access(candidate, os.X_OK)):
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def find_node(path_env=None, home=None, homebrew=HOMEBREW_NODE, minimum=MIN_NODE):
    """Return the first candidate node whose version is >= minimum, else raise NodeNotFound."""
    tried = []
    for candidate in node_candidates(path_env, home, homebrew):
        version, text = node_version(candidate)
        if version is not None and version[:2] >= tuple(minimum):
            return str(candidate)
        tried.append(f'  {candidate}: {text}')
    need = '.'.join(str(part) for part in minimum)
    listing = '\n'.join(tried) if tried else '  (no node executable found)'
    raise NodeNotFound(
        f'Node >= {need} is required for the Python-to-Desktop ActionRequest contract test, '
        f'but no candidate qualifies. Candidates found (PATH, then ~/.hermes/tools/node-*/bin, '
        f'then {homebrew}):\n{listing}'
    )
