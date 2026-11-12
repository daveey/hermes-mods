# Test stability fixes: resize redraw, pack sprawl, boards poll

Patches: **2573, 2622, 2629, 2644**.

## Behavior

Test-only: no runtime behavior change. Each entry removes a CI flake in one test:

- 2573: `test_a_resize_back_waits_until_the_program_answered_the_step` (terminal resize-back redraw) failed with the program reading an unchanged size. The watcher programs printed a `TICK` every 200 ms, and any output counts as the program's answer to the first resize step; a tick written before the step but read after it let the step back go early. The watchers now print only what a resize causes (`RESIZE` / `WINCH`), the answer bound `_RESIZE_ANSWER_SECONDS` is monkeypatched to 60 s for this test, and the test ends at `RESIZE 24` or once a `WINCH` shows a missed size.
- 2644: the same test, a second race. Python's `print('READY')` is two writes (text, then newline); a reader that took `READY` alone sent the resize pair while the newline was unread, and that newline counted as the answer. The test now waits for `READY\r\n`.
- 2622: `test_repacks_at_threshold` (worktree self-heal pack-sprawl) saw too few packs because each fixture commit ran `git maintenance run --auto`, which newer git or a runner gitconfig can use to repack. The fixture commits now pass `-c maintenance.auto=false -c gc.auto=0`.
- 2629: `test_replaced_board_file_reads_fresh_and_removed_board_is_dropped` (boards poll counts) asserted the recreated board file has a different inode; once the old file's last fd closes, the filesystem may reuse the number. The test now holds the old file open across the replace. A new test, `test_replaced_board_file_with_reused_inode_reads_fresh`, forces every file to report the same `(dev, ino)` and checks counts still read fresh.

## How it works

Test changes only, in the files listed under Code. No production module is touched.

## Code

`tests/hermes_cli/test_host_terminal_redraw.py` (2573, 2644), `tests/hermes_cli/test_worktree_selfheal.py` (2622), `tests/plugins/test_kanban_boards_poll_counts.py` (2629).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): the three files above. Entry paths: the web host terminal session and PTY bridge (`_pump_input`, python and node watchers; the node case skips without `node`; POSIX only), `maintain_pack_health` fixture packs in a temp git repo, and the kanban boards poll module with a probe. The commit messages report flake repros (starved runner 3/20 before, 0/60 after; 100/100 local runs for the inode test), which were not reproduced in this mirror.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The 60 s answer bound in 2573 is test-local (monkeypatched); production keeps its own value.
- 2622 only protects the fixture; real repositories still run auto maintenance.

## Plugin

Core change; no plugin required.
