# Display-only transcript compression

Patch: 0666 (07fb47660c), 0670 (3bb0324ccc).

## Cached-click follow-up (0670)

The sidebar click prepaints cached transcript rows synchronously before routing into the main chat. Already-selected/tiled sessions remain focus-only; asleep rows keep their read-only path. `prepaintClickedTranscript` lives in `apps/desktop/src/app/session/hooks/use-session-actions/provisional-transcript.ts` and is called by the sidebar's `use-offline-fallback.ts`.

Source regression suites: `prepaint-clicked-transcript.test.ts` and `offline-fallback.integration.test.tsx` in the corresponding directories. These frontend tests were not rerun by this mirror worker; no live click-latency measurement is claimed. This is display-only caching, not a modification to persisted conversation history.

## Behavior

A transcript GET marked `X-Hermes-Display-Transcript: 1` omits redundant provider reasoning fields for display. The Desktop client sends the display header, accepts gzip, and decodes gzip or an older backend's plain response. Other API clients retain the unmodified transcript.

## How it works

The session message route builds a display-only response and optionally gzips it. The Desktop transport marks transcript requests and decodes compressed replies. Stored messages are not changed.

## Code

`hermes_cli/web_routers/sessions.py`; `apps/desktop/electron/display-transcript.ts`; `apps/desktop/electron/main.ts`.

## Tests

`scripts/run_tests.sh tests/hermes_cli/test_session_messages_display_transcript.py`: three passing backend tests in this mirror run. The commit also contains `apps/desktop/electron/display-transcript.test.ts`; its frontend suite was not rerun by the mirror worker. Live Desktop behavior was not independently reverified by the mirror worker.

## Applying

Apply after 0665. Included to keep the public append contiguous before the card-wait fallback.

## Gotchas

This is a display transport optimization, not permission to strip provider replay fields from stored messages or inference history. It changes core API and Electron transport, not a disk plugin.
