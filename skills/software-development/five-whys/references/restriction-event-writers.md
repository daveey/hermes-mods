# Restriction-event writer inventory

For a new restriction-event kind, test lease-before-marker ordering, renewal, already-held grant shortcuts, home task deletion, event GC on old terminal tasks, and cleanup after a rejected request. A refusal can still be followed by a separate placement-writing transaction. Refuse publication over outstanding custody rather than silently revoking it unless revocation is explicitly part of the contract.

Cache held-task classification once per bounded GC batch: repeating a task-history scan for every candidate can turn retained-history maintenance quadratic even when the candidate query stays bounded. Exercise large retained histories and unrelated collectible controls, not only a single held row.
