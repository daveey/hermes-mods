# Visible dependency tree guides

Assert computed guide width/style in mounted hierarchy tests, not only `data-tree-guide` presence. Desktop publishes `--dt-border`; an unresolved legacy `--border` in a border shorthand computes to `0px none` and silently removes guides while indentation tests stay green.

Keep width/style independent of color resolution with current-token, legacy-token and explicit color fallbacks. Exercise the actual renderer in Chromium with current light/dark, legacy and missing tokens; require positive border width, solid style, nontransparent color and positive height. Inspect the fixture PNG. Browser fixture pixels remain distinct from deployed native proof; a native capture must bind the current model and loaded renderer to the actual screenshot.
