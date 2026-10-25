#!/usr/bin/env bash
# lib.sh — shared helpers for land-on-clever-cloud shell scripts (sourced, bash 3.2 compatible).

LOC_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOC_SKILL_DIR="$(cd "$LOC_SCRIPTS_DIR/.." && pwd)"

loc_die() { printf 'error: %s\n' "$*" >&2; exit 2; }
# loc_need "$@" in an option parser: the option in $1 needs a value in $2 (usage error, not "unbound variable")
loc_need() { [ $# -ge 2 ] || loc_die "$1 needs a value (see --help)"; }

# Print the help block: the leading comment lines of the calling script (after the shebang).
loc_help() {
  awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "$1"
}

# git guard: resolves the repository toplevel for a path, refuses $HOME and non-repos.
# Sets LOC_TOP and LOC_PREFIX (sub-folder of the app inside the repo, may be empty).
loc_git_guard() {
  local path="${1:-.}"
  [ -d "$path" ] || loc_die "not a directory: $path"
  LOC_TOP="$(git -C "$path" rev-parse --show-toplevel 2>/dev/null)" || {
    printf 'BLOCK not a git repository: %s — Clever Cloud deploys a committed git tree; ask the developer to git init + commit.\n' "$path" >&2
    exit 3
  }
  local home_real top_real
  home_real="$(cd "$HOME" 2>/dev/null && pwd -P)"
  top_real="$(cd "$LOC_TOP" && pwd -P)"
  if [ "$top_real" = "$home_real" ]; then
    printf 'BLOCK git toplevel is your HOME (%s): this folder has no repository of its own. Deploying from here would push the home directory. Ask the developer to git init the project folder.\n' "$LOC_TOP" >&2
    exit 4
  fi
  LOC_PREFIX="$(git -C "$path" rev-parse --show-prefix 2>/dev/null)"
  LOC_PREFIX="${LOC_PREFIX%/}"
  git -C "$LOC_TOP" rev-parse --verify HEAD >/dev/null 2>&1 || {
    printf 'BLOCK no commit yet in %s: nothing can be deployed (clever deploy pushes the committed HEAD).\n' "$LOC_TOP" >&2
    exit 5
  }
}

# Mask anything that looks like a credential (stdin -> stdout): the ONE masker, lib/common.py mask().
loc_mask() { python3 -I "$LOC_SCRIPTS_DIR/lib/common.py" mask; }

# Default location of the skill's working folder in the developer's repo (git-excluded).
loc_landing_dir() { printf '%s/.clever-landing' "$1"; }
