#!/usr/bin/env bash
# Install the land-on-clever-cloud skill for Claude Code and for Codex (and any agent reading ~/.agents/skills).
set -euo pipefail

src="$(cd "$(dirname "$0")" && pwd)/skills/land-on-clever-cloud"
[ -f "$src/SKILL.md" ] || { echo "skills/land-on-clever-cloud/SKILL.md not found next to install.sh" >&2; exit 1; }

for dest in "$HOME/.claude/skills" "$HOME/.agents/skills"; do
  mkdir -p "$dest"
  if [ -d "$dest/land-on-clever-cloud" ]; then
    echo "replacing $dest/land-on-clever-cloud (previous copy moved to $dest/.land-on-clever-cloud.previous)"
    rm -rf "${dest:?}/.land-on-clever-cloud.previous"
    mv "$dest/land-on-clever-cloud" "$dest/.land-on-clever-cloud.previous"
  fi
  cp -R "$src" "$dest/land-on-clever-cloud"
  echo "installed: $dest/land-on-clever-cloud"
done

command -v clever >/dev/null 2>&1 || echo "clever-tools missing: npm install -g clever-tools (or brew install CleverCloud/homebrew-tap/clever-tools)"
for t in git python3 curl; do command -v "$t" >/dev/null 2>&1 || echo "$t missing: required"; done
for t in psql pg_dump; do command -v "$t" >/dev/null 2>&1 || echo "$t missing: needed only for Supabase migrations"; done
echo "Command syntax of the CLI: the official skill — npx skills add CleverCloud/clever-tools"
