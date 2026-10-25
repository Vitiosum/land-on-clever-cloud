# Zones — two paths from the first question

The first question, before any survey output, is: **"Does this app store or process health
data, or might it later?"** It decides the zone, and the zone decides the catalogue.

| Answer | Path | Zone | What changes |
|---|---|---|---|
| no | non-HDS | `par` (Paris) | nothing about health in the proposal |
| yes | HDS | `parhds` (Paris HDS) | HDS prerequisites shown first; only services of the zone |
| not sure / maybe later | HDS | `parhds` from day one | changing zone later is a migration (new add-ons + data copy) |

**Paris first.** `par` outside health, `parhds` for health. `rbxhds` / `grahds` (OVHcloud
datacentres) only as a motivated alternative (`plan.py --zone-reason`): Clever's own advice is
better performance in Paris, notably for Cellar. Other zones (`rbx`, `scw`, `ldn`, `mtl`, `sgp`,
`syd`, `wsw`) also need a reason. The zone is chosen at creation: `provision.sh` always passes
an explicit `--region` (without it, `clever addon create` defaults to `par`).

## HDS prerequisites (shown before any proposal)

1. An **HDS contract** signed with Clever Cloud, attached to an **organisation**. The zone alone
   is not compliance; the certified scope is confirmed in the contract.
2. Only services **available in the zone** are proposed — `plan.py` reads them live.
3. Every database is created **encrypted at rest on a dedicated plan**; the DEV plan is refused.
4. Optional: a second, non-HDS organisation for components that process no health data (below).

The skill **never qualifies compliance**. It proposes, describes the boundary, and routes the
validation to Clever Cloud's legal team and the customer's DPO.

## How availability is read (never recalled)

```bash
clever addon providers show <provider-id> --format json   # "regions": [...], "plans": [...]
```

`plan.py` does this for every service it proposes and labels it `[observed] catalog live`. The
answer is cached for 24 h in the user's cache folder (`$XDG_CACHE_HOME` or `~/.cache`, never in a
git working tree: a committed cache would be the repository's word) and a cached answer is labelled
`[observed] catalog read <date> (cache)`. If the CLI cannot answer, it falls
back to `rules/_zones.json` and labels it `[read] snapshot 2026-10-24`.

Snapshot of 2026-10-24 (17 providers, clever-tools 5.2.0) — for orientation only:

| Service | par | parhds | rbxhds | grahds |
|---|---|---|---|---|
| PostgreSQL, MySQL, Redis, Elasticsearch | yes | yes | yes | yes |
| Cellar (S3) | yes | yes | yes | **no** |
| Pulsar, Config provider | yes | yes | no | no |
| Metabase, Otoroshi, Jenkins | yes | **no** | yes | yes |
| Keycloak, MongoDB, Matomo, FS Bucket | yes | **never in HDS** | | |
| Materia KV, MailPace, Azimutt | `par` only | **never in HDS** | | |
| Clever Kubernetes (CKE) | | **never in HDS** | | |

When a service is absent from the chosen zone, `plan.py` uses its `fallback_absent`
(`rules/_services.json`): MongoDB → PostgreSQL JSONB (rewrite, L) · Keycloak → in-app
authentication in the HDS zone (L) · FS Bucket → Cellar (code change) · Cellar in `grahds` →
Cellar in `parhds` · Metabase in `parhds` → Docker app in the zone · MailPace → keep the current
provider · Matomo / Azimutt → drop or keep the current tool · Materia KV → Redis add-on.

## Option: two organisations (HDS path only)

**Status: internal presales doctrine from call notes — to be confirmed by Clever Cloud. Never
present it as an acquired fact.**

The HDS contract and licence are attached to a Clever Cloud organisation. Components that
process **no health data** may live in a second, **non-HDS organisation** (zone `par`), without
the HDS licence or coefficient. The HDS organisation (`parhds`) keeps the app and anything that
stores or carries health data.

Offer it only when the plan needs a service absent from HDS zones, with the boundary per service:

| Service | Boundary condition (all of it must hold) | Risk |
|---|---|---|
| MailPace | no health content in e-mails: only a link back to the HDS app | conditional |
| Matomo | no health data and no patient identifier in URLs, page titles or events; IP addresses anonymised | conditional |
| Materia KV, Azimutt, internal tools | no health data, not even cached | conditional |
| Keycloak | a user's identity in a health app can itself reveal health information | **AT RISK** |

Keycloak: default is **in-app authentication in the HDS zone**. The second organisation only after
**written validation by Clever Cloud's legal team and the customer's DPO** (`plan.py --two-org auth
--legal-validated`). MongoDB, FS Bucket, Cellar hold application data: no second-org option.

Mechanics:
- `plan.py --health yes --two-org stats` moves that component to the second org (`par`) and
  prints the boundary in the Vision and in "Not verified yet".
- `provision.sh --org <hds-org> --second-org <non-hds-org> --dry-run` shows both organisations;
  it refuses to run if a component is planned in the second org and `--second-org` is missing.
- An add-on of another organisation cannot be linked: its credentials reach the HDS app as
  variables (`env-safe.sh push` from a local untracked file), never displayed.

## Price

The public grid (`price.py --zone parhds`) returned the same runtime prices for `par` and
`parhds` on 2026-10-24. Any HDS-specific cost is in the HDS contract, not in this grid: say so.
