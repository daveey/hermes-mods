# Home I/O test guard checks SQLite URI filenames

Patches: 1290 1291.

## Behavior

The test-suite real-home I/O guard (`tests/home_io_guard.py`) now also inspects SQLite URI filenames (`file:...` forms and keyword `database=` forms): a test that opens a protected database through a URI is denied from a checkout cwd, while reading an isolated replica from a protected cwd is allowed. 1291 adds refusal of encoded Windows drive forms (percent-encoded drive letters).

## Code

`tests/home_io_guard.py`.

## Tests

`tests/hermes_cli/test_real_home_tripwire.py`: `test_sqlite_uri_reads_isolated_replica_from_protected_cwd`, `test_sqlite_uri_denies_protected_database_from_checkout`, `test_sqlite_uri_denies_encoded_windows_drive`. This mirror task did not execute them.

## Applying

Apply 1290 then 1291. Test-only.

## Gotchas

Before this, `sqlite3.connect("file:...", uri=True)` bypassed the guard. No plugin involved.
