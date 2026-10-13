#!/usr/bin/env bash
# Install every mod in this repo into a Hermes home (default ~/.hermes).
#
#   ./install.sh                 link + enable all plugins, desktop plugins and skills
#   ./install.sh --community     ...and install the pinned community plugins in hermes-pack.yaml
#   ./install.sh --extras        ...and the optional ones in hermes-pack-extras.yaml (Home Assistant, Spotify)
#   ./install.sh --copy          copy instead of symlink (freeze this version; `git pull` won't update it)
#
# Symlinks mean `git pull` in this clone updates the mods in place. Re-running is safe.
# Core (Hermes source) changes are NOT installed by this script; see core/README.md.
set -euo pipefail

M="$(cd "$(dirname "$0")" && pwd)"
H="${HERMES_HOME:-$HOME/.hermes}"
mode=link community=0 extras=0
for a in "$@"; do
  case "$a" in
    --copy) mode=copy ;;
    --community) community=1 ;;
    --extras) extras=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
command -v hermes >/dev/null || { echo "hermes not on PATH" >&2; exit 1; }
mkdir -p "$H/plugins" "$H/desktop-plugins"

place() { # src dst
  local src="$1" dst="$2"
  if [ -L "$dst" ]; then rm "$dst"
  elif [ -e "$dst" ]; then echo "  skip $(basename "$dst"): $dst exists and is not a link (move it away to install)"; return 1
  fi
  if [ "$mode" = copy ]; then cp -R "$src" "$dst"; else ln -s "$src" "$dst"; fi
}

echo "agent plugins -> $H/plugins"
for d in "$M"/plugins/*/; do
  n="$(basename "$d")"
  if place "${d%/}" "$H/plugins/$n"; then
    echo "  $n"; hermes plugins enable "$n" >/dev/null 2>&1 || echo "  (enable $n by hand: hermes plugins enable $n)"
  fi
done

echo "desktop plugins -> $H/desktop-plugins"
for d in "$M"/desktop-plugins/*/; do
  n="$(basename "$d")"
  place "${d%/}" "$H/desktop-plugins/$n" && echo "  $n"
done

echo "skills -> skills.external_dirs"
cur="$(hermes config get skills.external_dirs 2>/dev/null || true)"
if printf '%s' "$cur" | grep -q "hermes-mods/skills\|$M/skills"; then
  echo "  already set"
elif [ -z "$(printf '%s' "$cur" | tr -d '[:space:][]')" ] || printf '%s' "$cur" | grep -qi "none\|null"; then
  hermes config set skills.external_dirs "[\"$M/skills\"]" >/dev/null && echo "  set to $M/skills"
else
  echo "  skills.external_dirs already has other entries; add $M/skills to it yourself:"; echo "$cur" | sed 's/^/    /'
fi

[ "$community" = 1 ] && { echo "community plugins (hermes-pack.yaml)"; hermes plugins pack install "$M/hermes-pack.yaml"; }
[ "$extras" = 1 ] && { echo "extras (hermes-pack-extras.yaml)"; hermes plugins pack install "$M/hermes-pack-extras.yaml"; }

echo
echo "Done. Restart the gateway / Desktop backend so backend plugins load (desktop plugins hot-reload)."
echo "Shortcuts and core changes: $M/shortcuts.md and $M/core/README.md"
