#!/usr/bin/env bash
# preflight.sh — gate 0, read-only: is it safe and possible to survey and land this repo?
#
# Usage: bash preflight.sh [app-folder] [--health no|yes|unsure] [--json]
#
# Checks: clever-tools installed / logged in (never runs `clever login`: it opens a browser),
# official clever-tools skill present, git toplevel is the project and not $HOME, not a worktree,
# HEAD exists, shallow clone (clever deploy refuses it: GATE with the fix), uncommitted changes
# (not deployed), current branch (POC goes on clever-poc),
# tracked .env files (names only), existing .clever.json link, .clever-landing/ git-excluded,
# .clever-landing/ or .clever.json tracked by git (BLOCK: the skill's state must not come from the repo).
# --health sets the path: yes/unsure → HDS (parhds, prerequisites shown first), no → par.
# Without --health it ends with the one question to ask before anything else.
# Lines: OK / WARN / BLOCK / GATE / ASK. Exit 0 green, 1 blocked, 10 ok but the DEPLOY is gated
# (shallow clone: survey, plan and provisioning can go on; apply the fix before clever deploy).

set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

APP="."; HEALTH=""; JSON=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) loc_help "$0"; exit 0 ;;
    --health) loc_need "$@"; HEALTH="$2"; shift 2 ;;
    --json) JSON=1; shift ;;
    -*) loc_die "unknown option $1" ;;
    *) APP="$1"; shift ;;
  esac
done
case "$HEALTH" in ""|no|yes|unsure) ;; *) loc_die "--health must be no, yes or unsure" ;; esac
RES="$(mktemp)"; trap 'rm -f "$RES" "$RES.err"' EXIT
rc=0; gate=0
say() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$RES"; [ "$JSON" -eq 1 ] || printf '%-5s %s\n' "$1" "$3"; [ "$1" = "BLOCK" ] && rc=1; [ "$1" = "GATE" ] && gate=1; return 0; }

# 1. clever-tools
if command -v clever >/dev/null 2>&1; then
  say OK cli "clever-tools $(clever version 2>/dev/null | head -1)"
  if clever profile >/dev/null 2>&1; then
    say OK login "logged in (profile readable; account not printed)"
  else
    say BLOCK login "not logged in — the developer runs 'clever login' in their own terminal (opens a browser), or exports CLEVER_TOKEN/CLEVER_SECRET. No account yet: sign up at https://console.clever-cloud.com/ and add a payment method there (the developer does it)."
  fi
else
  say WARN cli "clever-tools not installed: the survey and the plan still work; install is the developer's call (npm i -g clever-tools)"
fi
SK=""
for d in "$HOME/.claude/skills" "$HOME/.agents/skills" "$HOME/.codex/skills" ".claude/skills"; do
  [ -d "$d" ] && ls "$d" 2>/dev/null | grep -qi 'clever-tools' && SK="$d"
done
[ -n "$SK" ] && say OK official_skill "official clever-tools skill found in $SK" || say WARN official_skill "official skill not found: suggest once 'npx skills add CleverCloud/clever-tools' (command syntax lives there)"

# 2. git (guards exit 3-5 inside loc_git_guard: catch them as BLOCK)
if ( loc_git_guard "$APP" ) 2>"$RES.err"; then
  loc_git_guard "$APP"
  say OK git "toplevel: $LOC_TOP${LOC_PREFIX:+ (app folder: $LOC_PREFIX → APP_FOLDER=$LOC_PREFIX)}"
  [ -f "$LOC_TOP/.git" ] && say WARN worktree "this is a git worktree: clever deploy refuses worktrees — use --branch from the main checkout"
  BR="$(git -C "$LOC_TOP" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  case "$BR" in
    HEAD) say WARN branch "detached HEAD — clever deploy --branch <branch>" ;;
    clever-poc) say OK branch "on clever-poc" ;;
    *) say OK branch "on $BR — POC changes, if any, go on a new branch clever-poc (never a commit on $BR)" ;;
  esac
  if [ "$(git -C "$LOC_TOP" rev-parse --is-shallow-repository 2>/dev/null)" = "true" ]; then
    if [ -n "$(git -C "$LOC_TOP" remote 2>/dev/null)" ]; then
      say GATE shallow "shallow clone: clever deploy refuses it ('repository is shallow'); survey and plan go on. Fix before the deploy (ask the developer; changes only git metadata): git fetch --unshallow"
    else
      say GATE shallow "shallow clone with no remote: clever deploy refuses it; survey and plan go on. Fix before the deploy: a root commit with the same tree as the POC branch, deployed as its own branch — ROOT=\$(git commit-tree \"clever-poc^{tree}\" -m 'Clever POC (root commit, same tree)') (no git identity: git -c user.name=\"Clever POC\" -c user.email=clever-poc@localhost commit-tree …); git branch -f clever-poc-deploy \"\$ROOT\"; clever deploy --alias <alias> --branch clever-poc-deploy. It is a SNAPSHOT: redo both commands after every new commit on clever-poc (see reference/troubleshooting.md)"
    fi
  fi
  if [ -z "$(git -C "$LOC_TOP" config user.email 2>/dev/null)" ] || [ -z "$(git -C "$LOC_TOP" config user.name 2>/dev/null)" ]; then
    HID="$(git -C "$LOC_TOP" log -1 --format='%an <%ae>' 2>/dev/null | tr -d '\000-\037' | sed 's/«/</g; s/»/>/g' | cut -c1-60)"; [ -n "$HID" ] && HID="«$HID»"   # repo text: data, never instructions
    say WARN git_identity "no git identity configured (local or global). Do NOT reuse the history's author${HID:+ ($HID)}: it is usually someone else (upstream). Commit — and commit-tree — with a neutral LOCAL identity: git -c user.name=\"Clever POC\" -c user.email=clever-poc@localhost commit …; never touch the global config; say it in the report, the developer takes the commits over with git commit --amend --reset-author (or git rebase --exec 'git commit --amend --no-edit --reset-author')"
  fi
  DIRTY="$(git -C "$LOC_TOP" status --porcelain 2>/dev/null | grep -c . | tr -d ' ')"
  [ "$DIRTY" = "0" ] && say OK tree "working tree clean" || say WARN tree "$DIRTY uncommitted change(s): NOT deployed (clever deploy pushes HEAD); never stash or commit them for the developer"
  TE="$(git -C "$LOC_TOP" ls-files | grep -E '(^|/)\.env(\.[A-Za-z0-9_-]+)?$' | grep -Ev '\.(example|sample|template|dist|defaults)$' | head -n 10 | tr -d '\000-\011\013-\037' | sed 's/«/</g; s/»/>/g' | cut -c1-120 | sed 's/.*/«&»/' | tr '\n' ' ')"
  [ -z "$TE" ] && say OK env "no .env file tracked" || say WARN env "tracked env file(s): $TE— values already in git history: if any is a secret, the developer rotates it (names only are shown by scan.sh)"
  TS="$(git -C "$LOC_TOP" ls-files -- .clever-landing .clever.json | tr '\n' ' ')"
  [ -n "$TS" ] && say BLOCK state "tracked by git: $TS— the skill's ledger and app links must not come from the repository (a committed ledger or .clever.json would decide what is forced or deleted). Someone committed it: do NOT just untrack it (git rm --cached leaves the forged file on disk). Read it, DELETE it (rm -rf -- .clever-landing .clever.json), commit the deletion on clever-poc, then run the skill again from the scan"
  if [ -f "$LOC_TOP/.clever.json" ]; then say ASK link "already linked to Clever app(s) (.clever.json): reuse them or create new POC apps?"
  else say OK link "not linked yet"; fi
  EX="$(git -C "$LOC_TOP" rev-parse --git-path info/exclude)"; case "$EX" in /*) ;; *) EX="$LOC_TOP/$EX" ;; esac
  grep -qx '.clever-landing/' "$EX" 2>/dev/null && say OK exclude ".clever-landing/ is git-excluded" || say OK exclude ".clever-landing/ will be git-excluded by 'ledger.sh init' (.git/info/exclude, no tracked file touched)"
else
  say BLOCK git "$(cat "$RES.err" | sed 's/^BLOCK //')"
fi
rm -f "$RES.err"

# 3. the one question that comes before everything else
case "$HEALTH" in
  "") say ASK health "Does this app store or process health data (or might it later)? yes / no / not sure — it decides the zone (par or parhds) before any proposal" ;;
  no) say OK zone "non-HDS path: zone par (Paris). Nothing about health in the proposal." ;;
  yes|unsure)
    say OK zone "HDS path: zone parhds (Paris first; rbxhds/grahds only as a motivated alternative). ${HEALTH:+$( [ "$HEALTH" = unsure ] && echo 'Not sure → parhds from day one: changing zone later is a migration.')}"
    say OK hds_prereq "before any proposal: (1) an HDS contract signed with Clever Cloud, attached to an organisation — the zone alone is not compliance; (2) only services available in the zone are proposed (plan.py reads them live); (3) databases created encrypted at rest, never on the DEV plan; (4) optional second non-HDS organisation only for components with no health data (internal doctrine, to be confirmed by Clever Cloud; validated by Clever legal + the customer's DPO)" ;;
esac

if [ "$JSON" -eq 1 ]; then
  python3 -I -c 'import json,sys
rows=[l.rstrip("\n").split("\t",2) for l in open(sys.argv[1]) if l.strip()]
print(json.dumps({"result":"blocked" if sys.argv[2]!="0" else ("ok (deploy gated)" if any(r[0]=="GATE" for r in rows) else "green"),"checks":[{"level":a,"id":b,"detail":c} for a,b,c in rows]},indent=2))' "$RES" "$rc"
else
  if [ $rc -ne 0 ]; then echo "RESULT blocked — fix BLOCK lines first"
  elif [ $gate -eq 1 ]; then echo "RESULT ok (deploy gated) — survey and plan can go on; fix the GATE line before clever deploy"
  else echo "RESULT green"; fi
fi
[ $rc -ne 0 ] && exit $rc
[ $gate -eq 1 ] && exit 10
exit 0
