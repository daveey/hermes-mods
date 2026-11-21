"""Put the Hermes core checkout on sys.path so the real-core reader tests run instead of skipping.

HERMES_CORE_DIR overrides the default ~/.hermes/hermes-agent (core needs ruamel.yaml: `--with ruamel.yaml`)."""
import os
import sys
from pathlib import Path

try:
    import hermes_cli  # noqa: F401
except ImportError:
    _root = Path(os.environ.get('HERMES_CORE_DIR') or Path.home() / '.hermes' / 'hermes-agent')
    if (_root / 'hermes_cli' / 'kanban_db.py').is_file():
        sys.path.insert(0, str(_root))
