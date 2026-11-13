# without_evidence: strip only the "Existing work checked" record

Patches: **3657, 3688**.

## Behavior
- The heading form `## Existing work checked` now strips only the heading, following blank lines and one paragraph, like the bold-label form. Later heading-less steps in the card body are still scanned for host markers; a longer record is scanned as work, so the card stays local (fail closed).
- CRLF and lone CR line endings no longer let the record run on to the next heading and hide a later host marker from the portability scan.
- Docs only: the claim that a worker-queue gateway never falls back to worker-first is qualified to creation decisions; on the hub a queue miss goes to the admission judge and a denied card can still get worker-first from pool routing.

## How it works
- 3657 changes the heading-form pattern in `hermes_cli/kanban_placement.py` to one paragraph, and updates docstrings/comments in `kanban_worker_placement.py` and two test comments.
- 3688 normalises `\r\n` and lone `\r` to `\n` at the start of `without_evidence`, before both the evidence and existing-work substitutions. The normalised text is only scanned, never stored.

## Code
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_worker_placement.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_existing_work_checked.py`: `test_heading_less_text_after_the_record_paragraph_is_still_scanned`, `test_crlf_blank_lines_still_end_the_record_paragraph`.
- `tests/hermes_cli/test_kanban_pool_queue.py`: comment qualification only, no new test.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A record longer than one paragraph is treated as work and may keep a card local.
