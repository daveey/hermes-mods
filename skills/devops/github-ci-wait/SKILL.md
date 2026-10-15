---
name: github-ci-wait
description: "Use when waiting for a pushed commit's GitHub CI or release. Waits on runs/tags that contain the commit, never the newest run."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [github, ci, actions, release, wait, background]
---

# Waiting for a commit's GitHub CI / release

## Rule

Never write your own watcher that follows "the newest run" (`gh run list -L 1`) or one specific run id
(`gh run watch <id>`). On a busy branch, concurrency groups replace pending runs and most end `cancelled`.
The run that actually builds your commit is often one started by someone else's later push. Newest-run
watchers miss it and wait for hours while the session looks idle. Ask instead whether any successful run, or
any release tag, *contains* your commit.

## Use the script

`scripts/wait_for_ci.py` (in this skill's dir). Run it from inside the repo checkout; it needs `git` and an authenticated `gh`.

```bash
S=<this skill dir>/scripts/wait_for_ci.py
python3 -B $S <sha>                                   # every workflow that ran on a commit containing <sha> passes
python3 -B $S <sha> --workflow ci.yml                 # just this workflow (repeatable)
python3 -B $S <sha> --workflow release.yml --tag-glob 'v*'   # released: a v* tag contains <sha>
python3 -B $S <sha> --once                            # one check: exit 0 done, 2 not yet
```

Exit codes: 0 done (prints the run URLs or tags); 1 stalled (nothing queued or running will finish it,
and the failing run's URL is printed); 2 timed out (default 120 min) or not yet with `--once`; 3 the sha isn't
on `origin/<branch>` (default `main`; `--branch` to change). `--dispatch` (requires `--workflow`) re-runs the
workflow via `gh workflow run` on a stall, at most twice, instead of exiting 1. Pass several shas to wait
until one run or tag contains all of them.

How to run it from an agent:
- Start it with `terminal(background=true, notify=true)` and end the turn. Don't run a foreground sleep loop:
  it hits the tool timeout. Don't poll a background job either.
- Before telling the user something is "waiting on CI/the release", run `--once`. It has often shipped already.
- On exit 1, investigate the printed run (`gh run view <id> --log-failed`). If an earlier commit by
  someone else broke it, say whose.
- Don't cancel another session's queued run to get yours through: the queued run will contain your
  commit too.

## Notes

- Workflows with `paths:` filters don't run for every commit. Without `--workflow`, the script waits on the
  workflows that have a run on a commit containing yours. With `--workflow X`, when X never runs it reports a
  stall after the 3-minute startup grace period plus two idle polls.
- Release tags are the most reliable signal when a repo publishes them: a tag on a commit that contains yours
  means CI built and released your change, whichever run did it.
- A repo with its own wrapper (e.g. a `tools/wait_for_release.py` that knows which product tags a path
  maps to) takes precedence; check the repo's AGENTS.md first.
- Tests: `python3 -B -m unittest discover -s tests` from this skill's dir.
