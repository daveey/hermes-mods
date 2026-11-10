"""Test-owned SQLite transactions with deterministic descriptor cleanup."""
from contextlib import closing, contextmanager
import sqlite3


@contextmanager
def transaction(*args, **kwargs):
    # sqlite3.Connection.__exit__ commits/rolls back but does not close.
    with closing(sqlite3.connect(*args, **kwargs)) as connection:
        with connection:
            yield connection
