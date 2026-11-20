# Mirror 5011 verification

## Scope

Appended 659 non-merge mails at ordinals 4353–5011, through frozen source
`b68667740337595f1a41fce0ae50fe3ff66550ec`, excluding upstream
`ea405e49a5e281558b3eeb3a13af66c6116cd0f2`. All 4352 previously published
patch blobs were independently compared against public origin and remain unchanged.
The range has 147 feature documents and 659 README coverage rows, including the
nearest blocked-ancestor annotation at 5010. Source feature tests are documented
as coverage only: this mirror pass did not execute those feature suites.

## Executed acceptance

The unchanged `verify_mirror.py` ran as the first command of the actual README
Applying Bash recipe. The same recipe then independently replayed all 5011 mails,
51 bridges and three ancestry-only markers, applied the snapshot-bound hostname
escape repair, and checked the repaired complete tree. Both returned success:

```json
{"ancestry_only": 3, "base": "ea405e49a5e281558b3eeb3a13af66c6116cd0f2", "bridges": 51, "merge_base": "b46e21495f6a1d5a2547e9f196b3e6fec041ea46", "patches": 5011, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "b68667740337595f1a41fce0ae50fe3ff66550ec"}
{"marker_compile": "pass", "overlay_blob": "d380a19079ca10ee9e79630423abb483384e667f", "source_tip": "b68667740337595f1a41fce0ae50fe3ff66550ec", "whole_tree": "pass"}
```

Eight snapshot escape-repair unit tests passed. The coverage checker passed.
Pre-commit privacy checks passed with the full private denylist and gitleaks.
Five exact file/rule/line gitleaks exceptions were independently reviewed:
route-key and task-authority fixture strings, and synthetic upstream redactor
fixtures; no broad rule or path exclusion was added.

## New bridge provenance

Before 4353, the bridge imports the clean merge-tree of the previous frozen tip
`5c9e6833b35266d7989cf607b13cf6eef77b6219` and upstream integration merge
`215f319d0aa252db17cf64cb7e2d61bc4c2f3a8e`, preserving newer mainline work.
Only `tools/kanban_tools.py` retains its previous-tip version at this boundary:
the upstream one-line redaction change overlaps the later branch refactor at 4474.
Before 4498, the second bridge restores that redaction through the actual merge
resolution at `2e4c8014046c2ee73b2c5b0eba495882608e5838`. Its complete resulting
file was compared to that merge's file, not guessed from a desired final state.
Both provenance merges are ancestors of their respective patch boundary. The raw
source-space suffix replay and the full normalized replay agree with the frozen tip.

## Normalization and limits

Exact whole-line mappings carry historical public normalization forward and
propagate newly scrubbed additions into later context/removals. Fingerprint
updates were derived from normalized source plus those mappings. Incident prose,
identifiers and unpublished filename slugs were scrubbed; replacement identifiers
were checked for collisions against each other and existing fixtures. Existing
public patches and bridges were not regenerated. The historical hostname escape
overlay remains a mandatory final step, not a rewrite of published mail.

Replay proves inventory and whole-tree equivalence under the reviewed policy;
it is not execution proof of every mirrored feature or live Desktop behavior.
