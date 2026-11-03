#!/bin/bash
# Print every Hermes home's skills/.usage.json on this host, each after a "### <path>" line.
for f in ~/.hermes/skills/.usage.json ~/.hermes/profiles/*/skills/.usage.json; do
  [ -f "$f" ] && { echo "### $f"; cat "$f"; echo; }
done
