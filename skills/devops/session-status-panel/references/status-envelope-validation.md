# Status-envelope validation

Match JavaScript safe-integer wire semantics in Python status-envelope validators: JSON round-trips may decode integer-valued numbers as floats, so accept finite, nonnegative integer-valued `int`/`float` values up to `9007199254740991`, but reject booleans, strings, fractions, negative/non-finite and unsafe values. Test real JSON round-trips and all three numeric fields.

When testing a missing canonical status, assert the derived header source and null status separately from row text; preserve answered-ask one-line recovery/dedup assertions. Test legacy-envelope rejection separately from canonical backend-authored labels, and run the whole Desktop-plugin glob, not only producer-to-DOM fixtures.
