"""Review-only strict explicit configuration rejection contracts."""
import json
import pytest
from test_explicit_home_acquisition import api, worlds, ctx_for


@pytest.mark.parametrize('sync', [{'boards': 'private'}, {'boards': 7}, {'links_reader': 42}])
def test_malformed_explicit_source_selection_is_rejected(api, worlds, sync):
    home = worlds['explicit']
    (home / 'config.yaml').write_text(json.dumps({'kanban': {'sync': sync}}))
    reader = api.CoreReaders(home_context=ctx_for(home))
    try:
        with pytest.raises((ValueError, TypeError)):
            reader.boards()
    finally:
        reader.release()
