"""Isolated real-core name resolution probe; never imports the user's other plugins."""
import importlib.util
import json
from pathlib import Path
import sys

core, root = sys.argv[1:]
sys.path.insert(0, core)
spec = importlib.util.spec_from_file_location('skill_publish_probe', Path(__file__).with_name('skill_publish.py'))
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
# These are the compact rows skill_manager_batch.py actually returns: no path field.
args = {'operations': [
    {'action': 'patch', 'name': 'sample', 'old_string': 'base', 'new_string': 'published'},
    {'action': 'write_file', 'name': 'sample', 'file_path': 'references/topic.md', 'file_content': 'topic'},
    {'action': 'patch', 'name': 'sample', 'old_string': 'published', 'new_string': 'published again'},
]}
result = {'success': True, 'operations_applied': 3, 'results': [
    {'name': op['name'], 'action': op['action'], 'file_path': op.get('file_path'), 'success': True}
    for op in args['operations']]}
print(json.dumps(module.written_paths(args, result, Path(root))))
