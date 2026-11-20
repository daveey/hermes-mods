# Mirror 5039 verification

Unchanged `core/verify_mirror.py` on frozen source `993b62c3fd04470f4c52754f2ebfaf5bbebfa855` with exclusion base `ea405e49a5e281558b3eeb3a13af66c6116cd0f2`, policy `mirror-policy-5039.json`, replay plan `mirror-replay-5039.json` (51 hash-pinned bridges, 3 ancestry-only markers, no new bridges in 5012–5039):

```json
{"ancestry_only": 3, "base": "ea405e49a5e281558b3eeb3a13af66c6116cd0f2", "bridges": 51, "merge_base": "b46e21495f6a1d5a2547e9f196b3e6fec041ea46", "patches": 5039, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "993b62c3fd04470f4c52754f2ebfaf5bbebfa855"}
```

Published 0001–5011 patch bytes were not rewritten. 5012–5039 are the missing non-merge headers after `b68667740337595f1a41fce0ae50fe3ff66550ec`. Incident dates/hostnames in added lines were replaced with same-length public placeholders; six historical fingerprints were re-pinned and seven new privacy fingerprints were added after listing every unexplained blob (normalized source vs applied differed only by those reviewed replacements). Overlay preimage `97c27b2b6302aae080665f04c15bc5fe77120fa8` / postimage `d380a19079ca10ee9e79630423abb483384e667f` is unchanged.
