# Software failure analysis example

Reference for skill `five-whys`: its SKILL.md links this file from the step that needs it.

## Example (software)

```
Problem: nightly export job failed 3 of the last 7 nights with "disk full" on worker-2 (job logs 10/12, 10/14, 10/17).
Why 1: /tmp hit 100% during export — evidence: df in job log, 99.8% at 02:14.
Why 2: export writes a 40 GB intermediate file to /tmp — evidence: export.py:88 tempfile.mkstemp().
Why 3: intermediate files from crashed runs are never deleted — evidence: 6 stale export-*.tmp files, oldest 10/03.
Why 4: cleanup runs only on success (finally-less code path) — evidence: export.py:120, cleanup after return.
Root (happened): temp-file lifecycle tied to the success path; no guarantee of cleanup on failure.
Root (not caught): no disk alert on workers, and no test runs export with a forced failure.
Recurrence: second time; 10/05 fix deleted the files by hand, removing the symptom only.
Countermeasures:
- symptom: delete stale files — proof: df 31%.
- root: tempfile in a context manager, cleaned in finally — proof: test_export_cleans_up_on_failure fails before, passes after.
- detection: worker disk >85% alert — proof: alert fired on a test fill.
```
