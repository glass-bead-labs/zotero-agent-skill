#!/usr/bin/env bash
# Install the zotero skill: vendor-neutral location (~/.agents/skills) + the path Claude Code reads (~/.claude/skills).
#   ./install.sh            symlink to this checkout (edits here take effect immediately)
#   ./install.sh --copy     copy instead (stable snapshot)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
src="$here/skills/zotero"
neutral="$HOME/.agents/skills/zotero"
claude="$HOME/.claude/skills/zotero"
mkdir -p "$HOME/.agents/skills" "$HOME/.claude/skills"

if [ -e "$neutral" ] && [ ! -L "$neutral" ]; then
  echo "refusing: $neutral exists and is a real directory; move it away first" >&2; exit 1
fi
rm -f "$neutral"
if [ "${1:-}" = "--copy" ]; then cp -r "$src" "$neutral"; else ln -s "$src" "$neutral"; fi

if [ -e "$claude" ] && [ ! -L "$claude" ]; then
  echo "refusing: $claude exists and is a real directory; move it away first" >&2; exit 1
fi
ln -sfn "../../.agents/skills/zotero" "$claude"
echo "installed: $neutral -> $(readlink -f "$neutral")"
echo "           $claude -> $(readlink -f "$claude")"
echo "check:     python3 -I $neutral/scripts/zotero.py selftest"
