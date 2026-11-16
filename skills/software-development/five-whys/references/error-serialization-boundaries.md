# Trace every exception-serialization boundary

When a failure shows a misleading message, match the exact error prefix in the log to the code that
formats it before naming a layer. A PermissionError lost errno/filename at two boundaries (launch
probe detail string, PM worker transport); the first attribution (worker only) was wrong because the
probe runs BEFORE the worker in the real launcher. Mocks injected above a transport never catch lost
metadata: add a real-subprocess regression per boundary.

For `terminal: BrokenPipeError`, recover the originating command and decoded output envelope before blaming transport. A CLI piped to `head` or `grep -m1` can raise while printing after its consumer exits; a later command's exit code (for example a GitHub authentication failure) is not that pipe's status. Reproduce with a real pipe whose reader is closed before child startup, testing buffered shutdown and unbuffered writes separately. Precheck the same command with a live output reader so missing fixtures cannot masquerade as cancellation. Cover pre-import fast paths as well as main dispatch, and preserve unrelated internal transport exceptions.
