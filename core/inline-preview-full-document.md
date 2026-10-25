# Whole-document inline previews

Patches: 0251.

## Behavior

Inline HTML previews request the complete document rather than a 512 KiB skim. Whole-file reads allow up to 16 MiB and reject larger documents. Frames grow up to 16000 pixels with feedback-loop protection.

## How it works

Electron readFileText full mode and remote read-text?full=1 share full-read semantics. readInlineDoc rejects truncated old-shell responses in favor of an attachment card. nextFrameHeight detects repeated viewport-coupled growth.

## Code

apps/desktop/electron/{hardening,main,preload}.ts; apps/desktop/src/lib/desktop-fs.ts; apps/desktop/src/components/assistant-ui/inline-preview-directive.tsx; hermes_cli/web_routers/files.py

## Tests

apps/desktop/electron/hardening.test.ts (700 KB file and limits); apps/desktop/src/lib/desktop-fs.test.ts (local/remote full flag); inline-preview-directive.test.ts (full/truncated/error and growth); tests/hermes_cli/test_web_server_fs.py (full read and 413).

Test files above are shipped regression coverage, not a claim that every suite was rerun. See [mirror-verification.md](mirror-verification.md) for this export’s execution results.

## Applying

Apply the ordered core series through the last listed patch using `git am -3`, starting at the fork/upstream merge-base; these are not independent patches.

## Gotchas

Browser visual rendering and packaged Electron execution are untested in this mirror. The height ceiling remains finite; this is not an unrestricted file reader.

## Plugin?

This changes core-owned behavior or its test harness; no disk plugin is required.
