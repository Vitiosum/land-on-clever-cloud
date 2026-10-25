# Output contract — Vision, Proof, Exit

Write in the developer's language (translate `plan.py`'s English, keep file paths, commands
and figures exact). Delete a line rather than pad it.

## A. Vision (one screen, ≤ 35 lines) — produced by `plan.py`, then lightly edited

```markdown
## <app> on Clever Cloud — <Ready | Ready with N changes | Partial landing | Not a fit today>
<one sentence: what lands natively, what becomes an add-on, what stays or is rewritten>

| Component | Today | On Clever Cloud | Effort | Evidence |
|---|---|---|---|---|
| <one row per component: target = native / addon / docker / cke / keep-saas / rewrite / drop> |

**Changes (N files):** one line per file: `file:line` — what (effort)
**What you gain:** 2–4 concrete gains true for THIS app
**What you lose / stays elsewhere:** said plainly, or "nothing functional"
**Config (env vars, no code):** names and non-secret values; secrets "generated, never shown"
**Not verified yet:** the [assumed] items the POC settles
**Add-on variables go to:** add-on → apps (multi-app) · **Deploy order:** workers first (multi-app)
**Files to migrate:** existing objects → Cellar bucket with `migrate-files.py` (storage rewrites)
**Watch:** committed .env (what to do with it) / hard-coded credentials (masked, never reused) / vulnerable deps
**Security:** OPEN RISK first when the source has no mechanism to fix a weakness (e.g. anonymous upload/delivery kept as on the source, with the next step — repeat it in the final report); then the source app's issues (upload token without auth, delivery without auth, `Bearer undefined`, client MIME) — reported only, never copied as-is; plus the trade-offs this plan introduces (e.g. NuGet signature checks off on the build VM)
**Compliance-ready:** zone · encryption at rest from creation · backups · what is still needed (HDS contract)
**POC:** N apps nano + build M, add-ons + plans, zone ≈ x €/h (live grid, read today) — 2 h ≈ y €; ~z €/month if never stopped
**Repo:** branch `clever-poc`, N files; nothing on <their branch>. `.clever-landing/` is git-excluded.

Reply: **go · go without <X> · report only**
Details: `.clever-landing/REPORT.md`
```

Rules: one recommended target per row (no menus); every row has an evidence label; the price is
the script's figure; never "compliant", only what is in place and what is missing. In the HDS
path, the two-organisation option appears as "Option for <component>: … boundary: … (internal,
to be confirmed by Clever Cloud)". No agent able to ask (autonomous run) → **report only**.

## B. `.clever-landing/REPORT.md` (detail, git-excluded)

Survey summary (`scan.sh`), every component with its evidence and why this rung, all changes
with their full text (`plan.json` → `components[].changes[].what`), variables (names only),
the provision dry run, the open points, the commands run (no values), next steps beyond the POC.

## C. Proof (after the deploy) — from `prove.sh`

```markdown
## It runs — https://<domain>

| Check | Result |
|---|---|
| Page served by the app | PASS — 200 in 0.3 s, contains "<marker>" |
| Real code path (<the route the code really uses>) | PASS / FAIL — status, marker |
| Build-time config in bundle (front-ends) | PASS — "<public host>" in <chunk> |
| Worker / logs (bounded, 20 min) | PASS — "<log marker>" · or the line that matters |
| To do by you | <e.g. sign in on your DEV project and create one item> |

Verdict: PROVEN | NOT PROVEN · commit <sha> on clever-poc · zone <zone> · nano × 1
Forced deploys: <every `REPORT:` line printed by deploy.sh, as is — "remote head X replaced by Y, backup in clever-backup/…"; or "none">
Reused resources: <add-ons/apps reused, with their verified plan and encryption (provision dry run)>
```

## D. Exit (always after the proof, and on any abort)

```markdown
### Stop paying / clean up
- Pause: `clever stop --alias <alias>` (each app) stops **compute only**.
- **Add-ons keep billing until they are deleted**: <add-ons with plans> ≈ <x> €/h (~<y> €/month), from
  `plan.json` → `price_addons_only`. At the end of a POC, recommend deleting them ("costs nothing"
  means deleted, not stopped).
- Delete everything (irreversible — run only on your explicit request): `bash <skill>/scripts/ledger.sh teardown-plan`
- Your repo: branch `clever-poc` (`git branch -D clever-poc` to drop it), `.clever.json`, `.clever-landing/`
- <source platform>: untouched (read-only)
```
