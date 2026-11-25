"""Temporary branch-only SQLite connection-mutex diagnostic, not a cause fix."""
import faulthandler
import sqlite3
import sys
import threading
import _sqlite3

print('SQLITE_IDENTITY', sys.version, sqlite3.sqlite_version, sqlite3.threadsafety,
      getattr(_sqlite3, '__file__', 'built-in'), flush=True)
print('SQLITE_BUILD', sqlite3.connect(':memory:').execute('pragma compile_options').fetchall(), flush=True)
faulthandler.enable()
faulthandler.dump_traceback_later(5, exit=True)
connection = sqlite3.connect(':memory:', check_same_thread=False)
entered = threading.Event()
def authorize(*args):
    if not entered.is_set():
        entered.set()
        # Yield GIL while retaining SQLite connection mutex: a getter which waits
        # for that mutex with the GIL held will prevent this callback returning.
        threading.Event().wait(.5)
    return sqlite3.SQLITE_OK
connection.set_authorizer(authorize)
def write():
    connection.execute('create table probe (value text)')
thread = threading.Thread(target=write)
thread.start()
assert entered.wait(2), 'authorizer never entered'
print('SQLITE_PROPERTY_START', flush=True)
print('SQLITE_PROPERTY_RETURN', connection.in_transaction, flush=True)
thread.join(3)
assert not thread.is_alive()
faulthandler.cancel_dump_traceback_later()
print('SQLITE_PROBE_FINISHED', flush=True)
