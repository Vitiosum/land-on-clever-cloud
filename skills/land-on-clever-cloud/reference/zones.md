# Zones — HDS or not, from the questions

The first question of screen 2 (`questions.py`) is **"Does this application handle health data? (yes / no / not
sure)"**, with the sub-question **"HDS contract signed?"** after yes or not sure. The first decides the target zone,
the second the POC's zone. `plan.py` applies this table; the answers come from `.clever-landing/answers.json`.

| Health data | HDS contract signed | Production | POC | What changes |
|---|---|---|---|---|
| no | — | `par` | `par` | nothing about health in the proposal; "can already run in HDS (parhds)" is shown as an asset |
| yes / not sure | no, or not answered | `parhds` | `par`, synthetic data only | HDS prerequisites first; only services of `parhds` in the HDS organisation; "HDS-ready architecture: yes" or "… missing <x>"; production blocked until the contract is signed |
| yes / not sure | yes (`[declared]`: the skill cannot check it) | `parhds` | `parhds`, synthetic data only | the `hds_contract` hand-off is gone; real data stays outside the skill |
| not answered | — | `parhds` [assumed] | `par` | health is taken as "not sure" [assumed], the safe side; DEV plans refused |

**Paris first, one HDS zone.** `par` outside health, `parhds` for health: Clever Cloud's own advice is better
performance in Paris, notably for Cellar (2026-10-24).
Never propose another HDS zone: `plan.py` refuses `rbxhds` and `grahds` (exit 2) and no output names them.
A customer who needs another HDS zone changes it himself. Other zones (`rbx`, `scw`, `ldn`, `mtl`, `sgp`, `syd`, `wsw`)
need `plan.py --zone-reason`. The zone is chosen at creation: `provision.sh` always passes `--region` (without it,
`clever addon create` defaults to `par`).

**No POC in `parhds` without the HDS contract** (2026-10-25): until it is signed, the POC runs in `par` with synthetic
data. It is still built **HDS-ready** (2026-10-25): the same plans as production, databases encrypted from creation,
only services present in `parhds`, so moving to `parhds` changes nothing in the architecture.

## HDS prerequisites (shown before any proposal)

1. An **HDS contract** signed with Clever Cloud, attached to an **organisation**. The zone alone is not enough; the
   certified scope is confirmed in the contract.
2. Only services **available in `parhds`** go to the HDS organisation (`plan.py` reads them live).
3. Every database is created **encrypted at rest on a dedicated plan**; the DEV plan is refused.
4. What needs no HDS goes to a second, non-HDS organisation (below).

The skill never qualifies a customer's HDS status. It writes "HDS-ready architecture: yes" or "HDS-ready
architecture: missing <x>". The legal questions (GDPR role, HDS activities, contract model) are never put to the
developer during the journey: they go to "To prepare for the HDS meeting" in the contact request
(`deliverables.py --kind contact`).

## How availability is read (never recalled)

```bash
clever addon providers show <provider-id> --format json   # "regions": [...], "plans": [...]
```

`plan.py` does this for every service it proposes and labels it `[observed] catalog live`. The answer is cached for
24 h in the user's cache folder (`$XDG_CACHE_HOME` or `~/.cache`, never in a git working tree: a committed cache would
be the repository's word) and a cached answer is labelled `[observed] catalog read <date> (cache)`. If the CLI cannot
answer, it falls back to `rules/_zones.json` and labels it `[read] snapshot 2026-10-24`.

Snapshot of 2026-10-24 (17 providers, clever-tools 5.2.0), for orientation only:

| Service | `par` | `parhds` |
|---|---|---|
| PostgreSQL, MySQL, Redis, Elasticsearch | yes | yes |
| Cellar (S3), Pulsar, Config provider | yes | yes |
| Metabase, Otoroshi, Jenkins | yes | **no** |
| Keycloak | yes | **no** |
| MongoDB, Matomo, FS Bucket | yes | **never in HDS** |
| Materia KV, MailPace, Azimutt | yes | **never in HDS** |
| Clever Kubernetes (CKE, beta) | — | **never in HDS** |

Keycloak, whatever the catalogue shows (decision of 2026-10-26, below): possible in HDS but billed extra, so never
recommended there; it goes to the second, non-HDS organisation.

When a service is absent from `parhds` and does not go to the second organisation (`--two-org none`), `plan.py` uses
its `fallback_absent` (`rules/_services.json`): MongoDB → PostgreSQL JSONB (rewrite) · Keycloak → in-app
authentication in the HDS zone · FS Bucket → Cellar (code change) · Metabase → a Docker app in the zone · MailPace →
keep the current provider · Matomo → drop or keep the current tool · Azimutt → not needed · Materia KV → the Redis
add-on.

## Two organisations — the default for what needs no HDS

**Decision of 2026-10-26.** The HDS contract is attached to one organisation. A component that needs no HDS goes,
by default, to a second, **non-HDS organisation** in `par`: Keycloak (possible in HDS, but billed extra, so never
recommended there), preproduction, internal tools. The HDS organisation (`parhds`) keeps the app and everything that
stores or carries health data. Each placement comes with its boundary condition; the customer's DPO checks it.

| Service | Boundary condition (all of it must hold) |
|---|---|
| Keycloak | identity data only: no health data in user attributes, tokens or claims (a user's identity in a health app can itself reveal health information: ask the DPO) |
| MailPace | no health content in e-mails: only a link back to the HDS app |
| Matomo | no health data and no patient identifier in URLs, page titles or events; IP addresses anonymised |
| Materia KV | no health data, not even cached |
| Azimutt | internal tool: no health data, not even cached or in schema samples |
| Metabase | only non-health data, or Metabase as a Docker app in `parhds` |
| MongoDB, FS Bucket | no health data (or files) in them; for health data: PostgreSQL JSONB, or Cellar in `parhds` with files encrypted application-side |

Mechanics:
- `plan.py` (health yes or not sure) places every add-on absent from `parhds` or not recommended there in the second
  organisation (`par`) by itself, with the boundary in the screens and REPORT.md. `--two-org a,b` places exactly those
  components; `--two-org none` places none (each takes its fallback).
- `provision.sh --org <hds-org> --second-org <non-hds-org> --dry-run` shows both organisations; it refuses to run when
  a component is planned in the second organisation and `--second-org` is missing.
- An add-on of another organisation cannot be linked: its credentials reach the HDS app as variables
  (`env-safe.sh push` from a local untracked file), never displayed.

## Price

The public grid returned the same runtime prices for `par` and `parhds` (`price.py --zone parhds`, 2026-10-24): the
HDS cost is in the contract, not in this grid. The HDS tariff is not on the public site but was communicated by Clever
Cloud on 2026-10-27 — **€200/month per HDS organisation + 1.4 × the resources placed in it** (apps, add-ons,
builds, Cellar); a second, non-HDS organisation stays at grid prices. With health data yes or not sure, each price
level shows its HDS figure beside the grid total, the service line states the rule with "communicated by Clever Cloud on
2026-10-27, not on the public pricing page — confirm on your quote/contract", a POC in `parhds` shows its €/h × 1.4
plus the monthly fee, and the go names the HDS realistic price. In an HDS zone, `provision.sh --dry-run` and the Supabase
module's `deploy-target.sh` print the HDS price from the same grid read (1.4 × the HDS organisation's items, the second
organisation at grid price, per hour, 2 h and per build, + €200/month), bound to the token: the yes names that price. Premium is shown as its public formula only, never in
a total, never as including HDS.
