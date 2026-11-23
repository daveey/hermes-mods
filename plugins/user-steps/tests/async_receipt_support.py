"""Wait for asynchronous delivery, never reinterpret a pending receipt as success."""
import time


def await_operation(store, owner, *, key=None, oid=None, state='delivered', timeout=3):
    deadline = time.monotonic() + timeout
    while True:
        result = store.operation(owner, key=key, oid=oid)
        if result['operation']['dispatchState'] == state:
            return result
        if time.monotonic() >= deadline:
            raise AssertionError(f'operation did not become {state}: {result["operation"]}')
        time.sleep(0.01)
