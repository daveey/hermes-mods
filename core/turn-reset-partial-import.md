# Reused turn worker reset skips a half-imported module

Patch: **1529** (`patches/1529-fix-gateway-a-reused-turn-worker-s-reset-skips-a-mod.patch`, fork `dee20bcec75f`).

## Behavior

- A gateway turn worker thread that is reused for the next turn resets its turn-keyed state first
  (`gateway.turn_thread_reset.reset_reused_thread_state`): it clears this thread's interrupt bit and reason, and drops
  its `thread:<tid>` entry from the sudo password cache.
- Before this patch, a reset that raced another turn thread's **first** import of `tools.interrupt` or
  `tools.terminal_tool_sudo` raised `AttributeError: partially initialized module ...` and retired the worker thread.
  Now the reset skips a module whose names are not defined yet and finishes normally.
- Once the module has finished loading, the reset clears its state as before: the thread's sudo cache entry is
  removed, and the interrupt bit and its reason are cleared.

## How it works

Python adds a module to `sys.modules` when its import **starts**, before the module body defines anything. The reset
used to treat "present in `sys.modules`" as "fully loaded":

- `tools.interrupt`: it now reads `getattr(sys.modules.get("tools.interrupt"), "set_interrupt", None)` and calls
  `set_interrupt(False, tid)` only if the function exists.
- `tools.terminal_tool_sudo`: it reads `_sudo_password_cache_lock` and `_sudo_password_cache` with `getattr`, and
  pops `thread:<tid>` under the lock only when **both** are present.
- The existing `_PER_TURN_THREAD_LOCALS` loop already used `getattr(..., None)` and is unchanged.

Skipping is safe because a module that has not finished its first import has not run any turn code, so it holds no
state for this thread.

## Code

- `gateway/turn_thread_reset.py`: `reset_reused_thread_state`.

## Tests

The patch adds these two tests to `tests/gateway/test_turn_executor_reuse.py`. **They were read from the diff and
not executed in this mirror pass.**

- `test_reset_skips_a_sudo_module_another_thread_is_still_importing`: holds the real `tools.terminal_tool_sudo`
  mid-import on another thread and asserts `_sudo_password_cache_lock` is not defined yet. It then calls the reset,
  which must not raise. After the import is released, the test seeds this thread's `thread:<tid>` cache entry and
  asserts a second reset removes it.
- `test_reset_skips_an_interrupt_module_another_thread_is_still_importing`: does the same for the real
  `tools.interrupt` with `set_interrupt` not yet defined. After the import is released, it sets an interrupt with a
  reason and asserts the reset clears both: `is_interrupted()` is false and `get_interrupt_reason()` is `None`.

Both tests use the helper `_hold_mid_import`. It removes the module from `sys.modules` and from its package
attribute, both through `monkeypatch` so they are restored afterwards. It then inserts a `sys.meta_path` finder whose
loader blocks in `exec_module` until the test releases it, imports the module on a daemon thread, and removes the
finder once the import has started.

No test covers the case where only one of the two sudo cache names is defined. That case is reasoned from the code
(both must be present), not tested.

## Applying

Apply in series order with `git am -3 --empty=keep` on the fork merge-base. The change depends on the earlier
reused-turn-executor work that added `gateway/turn_thread_reset.py` and `tests/gateway/test_turn_executor_reuse.py`.
No config is needed.

## Gotchas

- Any new module-level state added to the reset must also be read with `getattr` and skipped when missing. A direct
  attribute read brings back the race.
- The fix skips work and does not wait for the import to finish. This is correct only because a half-imported module
  has no turn state yet.

## Plugin?

No. This is a core gateway change.
