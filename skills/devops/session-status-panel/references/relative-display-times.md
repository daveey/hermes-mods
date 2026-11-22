# Relative display times

Keep classification and wording backend-owned, but do not hash formatted display ages into the feed model. Use `reduce_panel(..., wire=True)` on every feed reducer entry path. Additive `clock_text` templates contain literal parts plus absolute Unix-second `{timestamp, format}` parts (`ago`, `elapsed`, `remaining`). Desktop's `clockRow` inserts ages on a local timer without network reads or cached-model mutation; session-tasks consumes the same bridge. Legacy string fields remain static fallbacks.

Keep execution evidence expiry, lifecycle and quiet/stuck classification server-owned. Clip literal tails without dropping a countdown behind a long gateway/hostname. Regress a two-minute clock advance through `/changes` using both revision and epoch; omitting epoch deliberately returns a reset/full response. Test actual reducer-to-DOM text at a pinned local time and preserve real source-change publication as a positive control.
