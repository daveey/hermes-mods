# Trace every exception-serialization boundary

When a failure shows a misleading message, match the exact error prefix in the log to the code that
formats it before naming a layer. A PermissionError lost errno/filename at two boundaries (launch
probe detail string, PM worker transport); the first attribution (worker only) was wrong because the
probe runs BEFORE the worker in the real launcher. Mocks injected above a transport never catch lost
metadata: add a real-subprocess regression per boundary.
