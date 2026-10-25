# HDS — reference sheet for a migration to Clever Cloud

> **Status: counter-reviewed.** 12 structuring claims, each submitted to a separate refuter,
> primary sources required — Legifrance for the law, esante.gouv.fr for the ANS framework and
> FAQs, cnil.fr, the hosting provider's API and documentation, the certificate PDF. A secondary
> source could not confirm.
>
> **None is confirmed without reservation.** All 12 are confirmed *with nuance*, and **seven
> carried a material error** — on points that commit you in front of a client. They are
> corrected here.
>
> Checked on 14 August 2026; zones and catalogue re-checked on 24 October 2026 (clever-tools
> 5.2.0). Health law moves: re-check dates and numbers before any signature.
>
> The sources are French. Quotations are translated; words whose occurrence was searched in a
> French text are kept in French.

> **Non-HDS track (no personal health data): this sheet does not apply, ignore it.** It only
> serves the HDS track of `modules/supabase/GUIDE.md`, phase 0.

---

## Status of this sheet

12 claims submitted to counter-review. **None is confirmed without reservation**: all 12 carry the status `confirmed_with_nuance`. Zero full refutation, zero point undeterminable in the strict sense — but several claims are wrong on one precise element, and those elements are the ones that commit you in front of a client.

| Status | Count | Detail |
|---|---|---|
| Confirmed without reservation | 0 | — |
| Confirmed with nuance | 12 | of which 7 with a material error to fix before reuse |
| Refuted | 0 (fully) | 5 specific elements refuted inside claims that are otherwise right |
| Undeterminable / to be decided | 4 open questions | see the last section |

**What is false and still circulates** (to be fixed in any sales material):

1. **"Three HDS zones"** — the platform API returns four with the `contract:hds` tag: `parhds`, `grahds`, `rbxhds` and `fr-north-hds` (https://api.clever-cloud.com/v4/products/zones). Only three are open to application deployment through the CLI.
2. **"Clever Cloud certified HDS v2.1"** — false as of 14 August 2026. The certificate reads "HDS - V2", the ANS lists Clever Cloud under "version 2.0", and v2.1 is not published. The pages clever.cloud/health-data-hosting and /fr/hebergement-donnees-de-sante show it wrongly (certificate PDF vs marketing pages).
3. **"HDS requires encryption at rest"** — false. Zero occurrence of « au repos » (at rest) in the v2.0 framework (HDS certification framework v2.0, 31 requirements).
4. **"HDS requires MFA"** — false. Zero occurrence of « multifacteur », « authentification forte », « MFA » in the v2.0 framework.
5. **"HDS sets a log retention period"** — false. No figure for a duration in the v2.0 framework, nor in v1.1, nor in the v2.1 draft.
6. **"The order of 26 April 2024 provides 24 months for migration"** — false. Article 2 of the order only provides six months (LEGIARTI000049538707). The 24 months come from ANS communications, not from the text.
7. **"A standard cloud framework contract is not enough"** — inaccurate as worded. R. 1111-11 governs the content, not the form: framework contract + **HDS annex** satisfies the text.

**The documentary trap, met in 9 of the 12 checks**: the PDF "Référentiel_certification_HDS - FR - V2.1 - version en concertation" is the first search-engine result on esante.gouv.fr. It is in revision mode (marks « a supprimé : v2.0 », requirement EXI 23 missing, history « arrêté du XXX »). It is **not** binding. The framework in force is v2.0 (order of 26 April 2024, JORF no. 0113 of 16 May 2024).

**Honestly not settled**: the current version of certificate FR094504 after the surveillance audits of December 2025 and 2026; the certified scope service by service and zone by zone; the application status of `fr-north-hds`; independent verification of the certificate with Bureau Veritas (the portal certifie.bureauveritas.fr only answers in POST, no verification succeeded).

---

## The point that changes the deployment

**Confirmed with a substantive correction.** The statement "three HDS zones" is false at the catalogue level; it is right at the application-deployment level.

### Actual state of the catalogue

| Zone | `contract:hds` tag | CLI application deployment | Infra |
|---|---|---|---|
| `parhds` | yes | yes | Clever Cloud, Paris (TH2, nLighten) |
| `grahds` | yes | yes | OVHcloud, Gravelines |
| `rbxhds` | yes | yes | OVHcloud, Roubaix |
| `fr-north-hds` | yes | **no** (no `for:applications` tag) | OVHcloud, France North |
| `par`, `scw`, `ldn`, `mtl`, `rbx`, `sgp`, `syd`, `wsw` | no | yes | outside the HDS scope |

The inconsistency on `fr-north-hds` is real and documented: it is tagged `contract:hds` in the API (https://api.clever-cloud.com/v4/products/zones), listed among the "Applications deployment zones" of the CLI reference (https://www.clever.cloud/developers/doc/reference/cli/), but absent from the inline help of `clever create --region` (still in clever-tools 5.2.0), without the `for:applications` tag that the three other HDS zones carry in the zones API, and with no documented DNS record (https://www.clever.cloud/developers/doc/administrate/domain-names/). Today it only serves some add-ons: Cellar, Metabase, Otoroshi.

### The operational trap

`par` (the default zone) **is not HDS**, although it runs in the same Paris datacenters as `parhds` and shares its outbound IP ranges (https://www.clever.cloud/developers/doc/cli/applications/). An application created without `--region parhds` is outside the certified scope even if it "is in Paris". There is no visible signal at run time: the app in `parhds` talks without complaint to a database that went to `par`.

### Second condition, always omitted

Deploying in an HDS zone **is not enough**. The Clever Cloud documentation states it explicitly: benefiting from certified hosting means deploying in an HDS zone **and** signing a specific contract (https://www.clever.cloud/health-data-hosting/). Section I of article L. 1111-8 of the Public Health Code (CSP) (version in force since 1 July 2025, law no. 2024-449 of 21 May 2024 art. 32) requires that "the service of hosting personal health data is the subject of a contract" (https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000049577902).

Wording to use in front of a client: *"HDS zone + signed HDS contract"*, never *"deployed in an HDS region, therefore compliant"*.

### What to ask, and whom

**To Clever Cloud's HDS / compliance team, before any commitment:**

1. Is `fr-north-hds` in the scope of certificate FR094504, and can it be ordered for applications, or is it reserved for add-ons? (inconsistency between API / CLI doc / inline help)
2. **The scope annex of the Bureau Veritas certificate** — exact list of the services and add-ons covered, zone by zone, as annexed to the HDS contract. Ask for the annex, not the marketing page.
3. What is the **current version** of certificate FR094504 and its sites annex on the day of signature? The published PDF dates from January 2025 and does not reflect the surveillance audits of December 2025 and 2026.
4. Why does the guarantees page cite MongoDB and Cassandra among the covered services when the CLI reference offers the MongoDB add-on in **no** HDS zone?

**To Bureau Veritas** (through the certificate's QR code or certifie.bureauveritas.fr): independent verification of certificate FR094504. No independent verification has succeeded to date.

---

## Regulatory obligations

Only the points of nature `regulatory_obligation`, confirmed or confirmed with nuance. The HDS certification framework is **not** of the same rank as the decree: it is approved by an order taken on the basis of R. 1111-10 CSP, so it is binding within the certification scheme and before the certification body, but it is never cited as an article of the CSP.

### In force as of 14 August 2026

| Requirement | Reference text | What it requires in practice |
|---|---|---|
| Mandatory hosting contract | L. 1111-8 I, last paragraph, CSP — version in force since 01/07/2025, law no. 2024-449 of 21/05/2024 art. 32 (LEGIARTI000049577902) | No health-data hosting service without a contract. Deploying in an HDS zone without an HDS contract = outside the scope. |
| Hosting provider holding a certificate | L. 1111-8 II CSP | The provider must be certified by an accredited body. Clever Cloud is on the ANS list (updated 07/08/2026) for "1,2,3,4,5,6 on version 2.0" (esante.gouv.fr/offres-services/hds/liste-des-hebergeurs-certifies). |
| 14 minimum clauses in the contract | R. 1111-11 I CSP — version from 01/04/2018 to 26/09/2026, decree no. 2018-137 (LEGIARTI000036658473) | Framework contract + **HDS annex** carrying the 14 clauses. The most structuring: 1° exact scope of the certificate; 3° hosting locations; 4° audit arrangements by the DPO; 6° indicators, guaranteed levels **and penalties** (an SLA without penalties does not satisfy 6°); 7° technical subcontractors; 8° control of access; 12° reversibility; 13° return of data; 14° destruction **after formal agreement** of the data controller. |
| Cascade effect | R. 1111-11 II CSP | A SaaS vendor relying on a certified hosting provider must **reproduce** these clauses in its own contract with the data controller. Cascading contractual architecture on three levels: healthcare facility / vendor / hosting provider. |
| GDPR article 28 DPA, in addition | Art. 28.3 (content, points a to h), 28.9 (written form, including electronic), 28.4 (sub-processor) — GDPR, directly applicable (cnil.fr/fr/reglement-europeen-protection-donnees/chapitre4) | Adds **content**, not necessarily a separate document: a single contract can carry both sets of clauses. The HDS framework itself recommends it (chapter 6, NOTE, reference to the SCCs of Implementing Decision (EU) 2021/915). Careful: it is 28.9 that requires writing, not 28.3. |
| Reversibility clause, minimum content | R. 1111-11 12° to 14° CSP **+** EXI 27 of the HDS framework v2.0, § 6.11 (referentiel_certification_hds---fr--v2.pdf) | Four mandatory elements: commitment to return all the information; destruction of every copy once returned; **method for calculating costs and deadlines**; **readable and usable return formats** for portability, and where relevant arrangements for moving VMs/containers. A clause "reversibility on terms to be agreed" satisfies neither R. 1111-11 nor EXI 27. Trigger: end of service **or** early termination "whatever the reason" (EXI 27), including loss or withdrawal of certification (12°). |
| Scope by activities, not by service model | R. 1111-9 CSP — version in force since 27/03/2026, decree no. 2026-209 of 24/03/2026 (LEGIARTI000036658481) | 6 activities (1° physical sites, 2° hardware infra, 3° virtual infra, 4° application hosting platform, 5° administration and operations, 6° backup **and electronic archiving**). Never write "HDS certified" without listing the activities. No ANS text states "PaaS = 3 + 4": it is a case-specific reading, the certificate is authoritative. |
| Electronic archiving in scope | R. 1111-9 6° amended, in force since 27/03/2026 | Any long-term retention offer now falls under activity 6 and therefore under HDS certification. The v2.0 framework still cites the old wording: **a real gap between the decree and the framework**, to be flagged. |
| Allocation of security responsibilities | EXI 13, § 5.8.1 of the HDS framework v2.0 | "The Hosting provider must plan and control the allocation of information security responsibilities between the Hosting provider and its client." The framework uses **neither the term nor the format "responsibility matrix"** — the written formalisation comes from chapter 8.1 of ISO 27001. RACI = an implementation good practice. |
| Client's verification rights | EXI 15, § 5.9.1 of the HDS framework v2.0 | Four rights, not two: (1) technical security audit **only on the resources specific** to the client, if any — not on shared resources; (2) management summary of an independent technical audit report on shared resources, less than three years old — **this is not the certification audit report**; (3) consultation of the access traces to personal health data (DSCP) held on its specific resources; (4) arrangements for consulting the latest HDS certification audit report. |
| Access traceability | § 2.1.2 of the v2.0 framework + EXI 15, EXI 16 | Process for granting and **annually reviewing** named, justified and necessary access rights; securing of the access procedure; collection and retention of access traces **and their reasons**; prior validation of interventions. Described as "intrinsic and mandatory to activities 1 to 4" — so binding on every certified hosting provider, not only for activity 5. |
| Obligation to log | Art. 32 GDPR + R. 1111-9 CSP | The obligation to log is binding. Only the **duration** is free on the HDS side. |
| Electronic identification of healthcare actors | Order of 28 March 2022 (JORFTEXT000045457991), art. L. 1470-2 and L. 1470-5 CSP, in force since 01/06/2022, original version still in force | Falls on the **provider of the digital health service / data controller**, not on the hosting provider. For "sensitive" services: Pro Santé Connect, CPx cards, approved MIE (electronic identification means), or MIE certified eIDAS substantial or high (EXI 03). **Two-factor transitional MIE are no longer allowed since 31/12/2025.** For non-sensitive services, these are only recommendations (§ 4.1.3). |
| Retention of medical records | R. 1112-7 CSP, version in force since 01/03/2018 (LEGIARTI000036658351) | 20 years from the last visit, extended up to the patient's 28th birthday, reduced to 10 years from death if death occurs less than 10 years after the last visit, periods suspended by any appeal that challenges medical liability. **Falls on the healthcare facility, not on the hosting provider** — but sizes the application traces. |
| Encryption at rest — health data warehouses | SEC-LOG-2, CNIL data-warehouse framework (deliberation no. 2021-118 of 07/10/2021, corrected by no. 2021-123 of 02/11/2021) | "Personal data must be encrypted at rest with algorithms and key sizes compliant with appendix B1 of the RGS. An operational key-management procedure must be formalised." **Conditional on the procedural route**: it applies on the declaration-of-conformity route; point 2.3 opens a specific authorisation request (art. 66 III of the French Data Protection Act, LIL) that allows defending alternative measures. **Narrower scope than common language**: excludes warehouses based on private legitimate interest, care processing by health professionals, processing based on explicit consent, and warehouses matched with the SNDS (points 1.3 and 2.6). |
| Warehouse cryptography, beyond rest | SEC-LOG-3, SEC-LOG-4, SEC-LOG-6, SEC-RES-3 — CNIL data-warehouse framework | Encrypted backups (RGS appendix B1); **separate keys** for directly identifying data and correspondence tables; **a specific key** for genetic or location-tracking data; encryption of all incoming, outgoing **and internal** flows. Volume encryption or a managed service's "encryption at rest" does **not** satisfy SEC-LOG-4 or SEC-LOG-6. |
| SNDS traces | Order of 6 May 2024, SNDS security framework, chapter 7.2 (JORFTEXT000049516244), repeals the order of 22 March 2017 | The only log duration actually imposed by a text: "Traces must be kept for a period of 6 months." |

### Firm deadline: 26 September 2026

Decree no. 2026-209 of 24 March 2026 (JORF no. 0073 of 26/03/2026, NOR SFHL2514369D, JORFTEXT000053717250), taken in application of art. 32 of the SREN law. Its article 3: entry into force the day after publication, **except 2° and 3° of article 1, deferred by six months**. 26 March + 6 months = **26 September 2026** (date shown by Legifrance; several secondary sources wrongly announce 27 September).

| Requirement | Text | What it requires |
|---|---|---|
| Storage exclusively in the EU/EEA | R. 1111-9-1 CSP, created by 2° of art. 1 (LEGIARTI000053720788) | "When the hosting activity […] involves storing these data, it is carried out exclusively on the territory of a Member State of the European Union or of a party to the Agreement on the European Economic Area." |
| Backups and archiving covered | R. 1111-9-1 **by reference** to R. 1111-9 6° | The word « sauvegarde » (backup) does **not** appear in R. 1111-9-1. Coverage results from the reference to "the hosting activity defined in articles R. 1111-8-8 and R. 1111-9", whose 6° covers backup and electronic archiving. Citing R. 1111-9-1 alone is an incomplete citation. |
| Remote access: allowed under conditions | R. 1111-9-1, second paragraph | Transfer "including remote access" to a third country remains lawful under an adequacy decision (art. 45 GDPR) or, failing that, under appropriate safeguards (art. 46 GDPR) with enforceable rights and effective remedies. The contract must then mention the absence of an adequacy decision and describe the safeguards precisely. **Storage is locked, not access.** |
| 15th contract clause, conditional | R. 1111-11 15° new, 3° of art. 1 | **Only applies if** the hosting provider or one of its subcontractors is subject to the law of a third country. Then: list of non-European regulations likely to impose a transfer or access, existence or absence of an adequacy decision, mitigation measures, residual risks. A strictly European hosting provider has nothing to write there — a differentiating argument, but to be proven along the **whole** subcontracting chain. |
| 4° and 8° rewritten | R. 1111-11 amended | Reinforced information on transfers **and remote access** from third countries. Remote access counts as a transfer. |
| Public map of transfers | R. 1111-11 III new | The hosting provider must **make public and keep up to date** the map of its transfers outside the EU/EEA. It is not a contract clause, it is a publication obligation on the provider. |

**No transitional regime is provided for contracts in progress.** Plan an amendment for the existing portfolio; do not assume that contracts signed before this date are covered.

### What is NOT required, contrary to a widespread belief

| Belief | Verified reality |
|---|---|
| HDS requires encryption at rest | False. Zero occurrence of « au repos » in the v2.0 framework; « chiffrement » (encryption) appears only once, in appendix 1 (SecNumCloud correspondence matrix), about **flows**. |
| HDS requires MFA | False. Zero occurrence of « multifacteur », « authentification forte », « deux facteurs », « MFA ». HDS refers to the risk analysis through EXI 01 (ISO/IEC 27001) and EXI 05. |
| HDS sets a log retention period | False. No figure for a duration. The duration is up to the data controller and is set in the contract (R. 1111-11). |
| Activity 5 was removed | False. The removal was announced by ASIP Santé on 04/04/2019 but **never transposed**: 5° still appears in R. 1111-9, and the March 2026 decree — which did amend 6° and create R. 1111-9-1 — did not touch it. The v2.0 framework § 2.1.2 partly neutralises it: if the hosting provider only performs these tasks as inherent to activities 1 to 4, it does not need certification for 5°. |
| Activity 6 covers all backups | False. Framework v2.0 § 3.1.4: "Activity 6, data backup, must be interpreted as covering only outsourced backups. Backups intrinsically necessary to activities 1 to 5 are within the scope of activities 1 to 5." Wrongly claiming activity 6 is a classic reason for a scope reduction in a renewal audit. |
| The hosting provider's certification covers the client's obligations | False, but **not absolutely**: for the hosted scope, it does cover the client's obligation under L. 1111-8 to use a certified hosting provider. It does **not automatically cover all** of its regulatory, application and GDPR obligations. The words "automatically" and "all" are load-bearing: removing them makes the sentence false and turns the argument against pre-sales. |

Encryption, MFA or duration obligations exist, but they come from elsewhere (CNIL data-warehouse framework, PGSSI-S order of 28 March 2022, contract). Attributing them to HDS loses the discussion as soon as a lawyer opens the framework.

---

## Good practices

Not binding. To be applied, but **never** presented as regulatory requirements — a DPO or a lawyer on the client side checks in three clicks, and the error discredits the rest of the file.

**Log retention periods.** No HDS source sets them. The benchmarks come from elsewhere and do not apply to the hosting provider:

- *Technical traces*: 6 months to 1 year. Source: CNIL recommendation, deliberation no. 2021-122 of 14 October 2021 (JO no. 0254 of 30/10/2021, CNILTEXT000044283637). The text itself says it "is neither prescriptive nor exhaustive". A ceiling of 3 years is accepted when an internal-control need justifies it. **Careful about the scope**: this recommendation targets **application** logging — "the logging mechanisms linked to the application on which the processing relies, and not perimeter logging". Presenting "6 months to 1 year" as the CNIL rule for technical traces inverts its field.
- *Application traces (access to patient records)*: 20 years, by reference to R. 1112-7 CSP. Source: ANS FAQ under the **Ségur du numérique en santé, wave 2** programme, theme "Requirements and evidence", updated on 21/02/2026 — it is addressed to **software vendors** (DPI, LGC-MdV, SGL, RIS, DUI, LGO, DRIMbox, PFI, CD), not to hosting providers. It is not an HDS FAQ.

**MFA for the hosting provider's administrators.** Not required by name by HDS, but expected in practice under the ISO 27001 risk analysis: EXI 05 requires explicitly considering the "failure to control granted access" and the "distribution of electronic identification means". To be positioned as an ISMS measure justified in the **statement of applicability** (control A.8.24 "Use of cryptography", A.8.5 "Secure authentication"), not as an HDS requirement. If the offer is SecNumCloud qualified, SecNumCloud brings the strong-authentication requirement.

**Encryption at rest outside the warehouse scope.** The bar is set by art. 32 GDPR, which cites encryption as an appropriate measure "where appropriate" — so proportionate to the risk — and by the data controller's DPIA. An architecture decision to document, not a box to tick. Many hosting providers handle with encryption at rest the risks that EXI 05 obliges them to consider (event A: failure of hardware media; event B: loss of control over media, in particular during "the reallocation of storage space") and declare it in their statement of applicability. **A question to ask the hosting provider; the answer must be obtained in writing**: it belongs to its offer, not to the framework.

**RACI format for the allocation of responsibilities.** EXI 13 requires the allocation, not the format. RACI is an implementation good practice. The only standardised table required by the framework is that of chapter 8 "Representation of guarantees" — actors, subcontractors, transfers outside the EEA — which does **not** cover the hosting-provider/client allocation.

**End-to-end rehearsal of reversibility.** Since the return deadline is contractually committed (EXI 27), run the export at least once and measure its real duration. A theoretical exit plan is not an exit plan.

**Vocabulary not to reuse.** Several ANS pages (produits-services/hds, labels-certifications) still describe two certificates, « hébergeur d'infrastructure physique » (physical infrastructure host) and « hébergeur infogéreur » (managed-services host). These terms have **zero occurrences** in the v2.0 frameworks: it is v1 (2018) vocabulary left online. Do not build contractual reasoning on it.

---

## What constrains the architecture

A section to handle at design time (phase 4 of `modules/supabase/GUIDE.md`), not in a final check. Each point below is hard or impossible to catch up on. The requirements that change an architecture if discovered late:

- **named access traceability** — a component to size from the start, not a box to tick: in the reference case, the audit log weighed more than the data (§5);
- encryption at rest and in transit, and **where the keys live** (§8);
- location of the backups, not only of production (§3);
- reversibility: which format, how long (§6);
- retention period, and effective purge (§5);
- subcontracting chain to document (§3, §4).

### 1. Zone choice: at creation, on every resource

The choice is made at `clever create --region` **and** at every `clever addon create --region`: both commands default to `par`. `par` is **not** HDS, despite Paris datacenters shared with `parhds` and shared outbound IPs. Only three zones are open to application deployment: `parhds`, `grahds`, `rbxhds`.

**`parhds` by default and recommended** (Clever Cloud recommendation: best performance in Paris, Cellar in particular). `rbxhds` and `grahds`, hosted by OVHcloud in Roubaix and Gravelines, are only offered as an alternative driven by a client reason (multi-site DR, choice of hosting provider) — and `grahds` has no Cellar.

A zone error is not final but it costs: zone migration (console then redeploy for an app, PHP to recreate; Migration Tool for PostgreSQL, MySQL, MongoDB, Redis; support for Cellar and Pulsar; FS Bucket cannot be migrated — https://www.clever.cloud/developers/doc/manage/zone-migration/), with downtime, and with no documented guarantee towards an HDS zone. **Until it is done, compliance is broken.**

Zones to exclude for any use touching health data, including ancillary use, in anticipation of R. 1111-9-1 (26/09/2026): `mtl` (Canada), `sgp` (Singapore), `syd` (Sydney), and **`ldn` (United Kingdom — outside the EEA since Brexit)**. `wsw` (Warsaw) is in the EEA but outside the HDS scope.

### 2. The add-on catalogue has gaps in HDS zones

This is the most structuring constraint and the least anticipated. Source: `clever addon providers show <id>`, surveyed on 2026-10-24 — to be surveyed again before each engagement.

| Add-on | Availability in an HDS zone |
|---|---|
| Application (all runtimes, Docker) | The three application HDS zones |
| PostgreSQL, MySQL, Redis, Elastic | The three application HDS zones |
| Cellar | `parhds`, `rbxhds`, `fr-north-hds` — **not `grahds`** |
| Metabase, Otoroshi, Jenkins | `rbxhds`, `grahds` — **not `parhds`** |
| Pulsar, config-provider | `par` / `parhds` only |
| **fs-bucket** | **No HDS zone** — no shared POSIX file storage, plan for Cellar |
| **Keycloak, MongoDB, Matomo, Materia KV, MailPace, Azimutt** | **No HDS zone** |
| **Clever Kubernetes (CKE)** | **No HDS zone**: public beta, three datacenters in Paris, no zone option in `clever k8s create`, no HDS mention in the docs |

Available in an HDS zone does not mean included in the certified scope: **the scope is confirmed with Clever Cloud, in the contract** (certificate annex, Q2 below).

Design consequences: a health architecture must be designed **around these gaps** from the start, or move the missing components out of HDS with an explicit, documented data boundary. The Keycloak case is particularly treacherous: IAM is often laid down as a base component and exists in no HDS zone.

**Architecture option: two organisations.** The HDS contract and licence are attached to a Clever Cloud organisation. Components that process **no health data** can live in a **second, non-HDS organisation** (zone `par`), with no HDS licence or coefficient (internal doctrine noted in pre-sales meeting reports, **to be confirmed by Clever Cloud before presenting it as settled**). The HDS organisation (`parhds`) keeps the application and everything that stores or carries health data. To be offered when the client needs a service absent from the HDS zones, with a boundary documented service by service:

| Service in the non-HDS organisation | Condition for it to stay there |
|---|---|
| MailPace (transactional emails) | no health content in the emails (no result, reason or date of care); link to the HDS app only |
| Matomo (audience measurement) | no health data or patient identifier in URLs, titles or events; anonymised IPs |
| Materia KV, Azimutt, internal tools | no health data, neither in clear nor in cache |
| Keycloak (identities) | **at risk**: the identity of a user of a health app can reveal health information. Only after written validation by Clever's legal team and the client's DPO; otherwise authentication in the application, in the HDS zone |

The module **never rules on** the compliance of this split: it offers it as an option, describes the boundary, and sends the validation to Clever Cloud's legal team and the client's DPO (internal rule: every HDS compliance question goes to legal).

Contradiction not resolved: the Clever Cloud guarantees page cites MongoDB and Cassandra among the covered services, while the CLI offers MongoDB in no HDS zone.

### 3. Location: production, replicas, backups, logs, acceptance

From 26 September 2026 (R. 1111-9-1), all storage resulting from a hosting activity must be exclusively in the EU/EEA. To audit explicitly, each element being an independent breaking point:

- region of the managed databases and volumes;
- **replication region** — a bucket or object storage whose redundancy leaves the EEA is non-compliant even if the primary region is European;
- destination region of backups, exports, snapshots;
- cold archive storage;
- logs containing health data;
- acceptance / test environments;
- second-tier subcontractors.

The most often missed point: an architecture compliant in production whose backups land outside the EEA is non-compliant, since backup and archiving are covered by the reference of R. 1111-9-1 to 6° of R. 1111-9.

### 4. Remote operations access = transfers

From 26/09/2026, remote access from a third country is treated as a transfer (8° amended). This targets L3 support, on-call, operations teams. **It is the point most often missed in architectures that are otherwise European.** The decree does not require bringing support back: it requires declaring it, framing it (art. 45 or 46 GDPR) and describing it in the contract.

To inventory from the design stage: actual location of the teams with technical access to the data, along the whole subcontracting chain.

### 5. Two logging regimes

Direct consequence of crossing HDS / R. 1112-7:

- **Technical logs**: rolling 6-12 month window, hot volume, automatic purge.
- **Application traces of access to patient records**: long-term archiving — potentially 20 years, extended up to the 28th birthday for minors and suspended in case of litigation. Requires dedicated object storage with versioning/immutability, integrity, timestamping, restorability, **a 20-year cost plan**, and a reversibility strategy that includes these archives.

HDS logging chain to wire separately (EXI 15, EXI 16): traces of access to personal health data (DSCP) **by the hosting provider's staff**, viewable by the client, audited internally, integrity-protected and exportable. Distinct from the client's application logs.

### 6. Reversibility: formats and a tested exit path

EXI 27 requires formats "readable and usable for portability purposes" and, where relevant, arrangements for moving VMs/containers. Design consequences:

- standard, documented export formats: native SQL dump for databases, S3-compatible objects for storage, portable container images;
- avoid dependencies on proprietary managed services that cannot be exported;
- plan a **tested** exit path, not a theoretical one;
- trigger to cover: early, unplanned termination, including provider failure or loss of certification (12° R. 1111-11) — not only the normal end date.

A proprietary export, an undocumented dump or a return billed "on quote" with no calculation method does not satisfy EXI 27.

### 7. Multi-region HDS: check add-on by add-on before promising a DR plan

`parhds` + `rbxhds` or `grahds` is possible for resilience. Check that **every** add-on of the plan exists in both zones before any DR commitment — see the gaps table above (Cellar missing from `grahds`, Metabase and Jenkins missing from `parhds`).

### 8. Encryption with separate keys if in the warehouse scope

SEC-LOG-4 and SEC-LOG-6 require **cryptographic separation by distinct keys**: separate keys for directly identifying data and correspondence tables on one side, health data on the other; a specific, isolated key for genetic or location-tracking data. Volume encryption or an "encryption at rest" enabled at the managed-service level is not enough.

This is designed at the application level or with separate instances and key sets **from the start**, not as a catch-up. The most under-costed item: the operational key-management procedure (rotation, escrow, revocation, separation of roles, reversibility).

### 9. Certification boundary to document from pre-sales

On Clever Cloud: the PaaS and the managed add-ons covered by the contract (scope to read in the certificate annex, not to assume) fall to the hosting provider; **the application deployed on it does not fall under its certification**. What stays with the client: application code, management of application identities and permissions, application-level encryption, business logs, GDPR compliance of the processing (legal basis, retention periods, DPIA, informing data subjects, exercise of rights).

Deliverable to produce from pre-sales (EXI 13), covering at least: access and permission management, encryption in transit and at rest, backups and restore, logging and retention of traces, vulnerability and patch management, incident response and breach notification, reversibility. This document is attached to the contract, it does not replace it.

### 10. Strong authentication: in the application, not in the hosting

Never write "our HDS hosting guarantees strong authentication of health professionals". MFA / eIDAS substantial MIE is handled in the application, by the vendor or the healthcare facility, through Pro Santé Connect / e-CPS / CPx card (OIDC), and it is they who sign the commitment to secure electronic identification.

**A calendar point not to miss in a 2026 quote**: two-factor transitional identification means are no longer allowed since 31 December 2025. A project that still plans "password + SMS OTP" for a sensitive service is non-compliant.

### 11. Binding geographical scope

Clever Cloud's HDS is limited to the 5 sites of the annex of certificate FR094504: Nantes (4 rue Voltaire — operations site), OVH Roubaix (2 rue Kellermann), OVH Gravelines (route de la Ferme Masson), nLighten / EXA Infrastructure (34 rue Gardinoux, Aubervilliers), Telehouse 2 (65 rue Léon Frot, 75011 Paris). Any other region or zone of the catalogue is outside the certified scope — **including for add-ons and backups**, not only for applications.

Favourable point: all 6 activities are covered, including activity 5 (administration and operations) and activity 6 (backup). So there is **no** need to rely on a second certified hosting provider for backup or for managed services of the hosting layer.

---

## What remains to be decided

None of these questions has a verified answer. Do not commit on them.

### To Clever Cloud — HDS / compliance team, before any client commitment

**Q1 — `fr-north-hds`.** "The zone `fr-north-hds`, tagged `contract:hds` in your v4 API and listed among the *Applications deployment zones* of your CLI reference but absent from the inline help of `clever create --region` (clever-tools 5.2.0), with no `for:applications` tag in the API, and absent from the domain-names page: is it within the scope of certificate FR094504? Can it be ordered for applications, or is it reserved for add-ons?"
*Stake*: an unresolved inconsistency between three official sources of the hosting provider. Do not offer this zone until it is settled.

**Q2 — Certificate scope annex.** "Provide the exact list of services and add-ons included in the certified scope, zone by zone, as annexed to the HDS contract." Ask for **the annex of the Bureau Veritas certificate**, not the marketing page.
*Stake*: the binding scope is the certificate's, not the service model's. "HDS certified" without a list of activities and services commits to nothing.

**Q3 — Current version of the certificate.** "What is the current version of certificate FR094504 and of its sites annex on the day of signature?" The published PDF dates from January 2025 (mention « Date de révision : - » = initial version). The surveillance audits of December 2025 and December 2026 may have changed the annex.
*Stake*: a scope contracted on an outdated annex.

**Q4 — MongoDB / Cassandra contradiction.** "Why does the page /fr/hebergement-donnees-de-sante cite MongoDB and Cassandra among the covered services, while the CLI reference offers the MongoDB add-on in no HDS zone?"

**Q5 — Encryption at rest.** "Is encryption at rest enabled on the managed add-ons in the HDS zone, and does it appear in your ISO 27001 statement of applicability under control A.8.24?" **Answer to be obtained in writing.**
*Stake*: the HDS framework does not require it; it is a property of the offer, to be established, not assumed.

**Q6 — Editorial correction.** The pages clever.cloud/health-data-hosting and /fr/hebergement-donnees-de-sante announce a certificate "under the v2.1 framework, in force since 16 May 2024". The date is right, the version label is wrong: the framework in force since 16 May 2024 is **v2.0**, and the ANS lists Clever Cloud under "version 2.0". To be fixed before any reuse in a tender response — a DPO or an auditor spots it in three clicks.

### To the ANS (hds@esante.gouv.fr or the consultation platform)

**Q7 — v2.1 calendar.** "Is the publication of framework v2.1 confirmed for October 2026, and its entry into application in December 2026 or three full months after publication?"
*Stake*: the ANS itself announces (news of 15/07/2026) "publication planned in October 2026" and "entry into application three months after its publication, i.e. in December 2026" — **the arithmetic is shaky** (October + 3 months = January 2027). These dates are forecasts not covered by an order as of 14 August 2026. Never present them as law in force.

**Q8 — Framework / decree alignment.** The v2.0 framework in force still cites the old wording of 6° of R. 1111-9 (« La sauvegarde des données de santé. » — "The backup of health data.") and has no longer been aligned with decree no. 2026-209 since 27 March 2026, which added « notamment leur conservation dans le cadre d'un archivage électronique » ("in particular their retention as part of electronic archiving"). Which reading applies in audit until v2.1?

**Q9 — Inconsistent transition period.** The ANS announces "24 months" in its press release of 16 May 2024 and "18 months" in its news of 20 November 2025, for the same deadline of 16 May 2026. No practical consequence (the deadline is identical and settled), but do not cite it with a duration: cite the date.

### To Bureau Veritas

**Q10 — Independent verification.** No independent verification of certificate FR094504 has succeeded: the portal certifie.bureauveritas.fr only answers in POST, and GET requests by case number (24357609) as by company name all return « Aucun résultat trouvé » (no result found), including on a control sample — which tests the mechanism, not the certificate. **The only source of the number FR094504, of case 24357609 and of the 5-site annex is the PDF published by Clever Cloud**; the ANS list publishes neither certificate number nor sites. Verification to be done through the certificate's QR code or a direct request to Bureau Veritas.

### To the CNIL ("Besoin d'aide" service)

**Q11 — Sufficiency of HDS certification under article 28.1 GDPR.** "Does using an HDS-certified hosting provider constitute on its own a sufficient guarantee within the meaning of article 28.1 GDPR, or must the data controller separately document the security measures of its processor?"
*Stake*: no primary CNIL page explicitly states that using an HDS hosting provider does not exempt from GDPR obligations — the page « Le RGPD appliqué au secteur de la santé » does not phrase it. The current reasoning relies on GDPR articles 24 and 28 combined with § 3.1.1 of the framework, which is enough in practice but is not a binding citation.

### To the lawyer who will sign

**Q12 — Exact wording of 15° and III of R. 1111-11.** The content of these future provisions was established by cross-checking the CSP section projected at 1 October 2026 with the text of decree no. 2026-209; the full verbatim text could not be obtained. **Have the exact wording re-read on Legifrance before freezing a set of clauses.**

**Q13 — Qualifying the warehouse scope.** The CNIL data-warehouse framework excludes from its field warehouses based on private legitimate interest, care processing by health professionals, processing based on explicit consent, and warehouses matched with the SNDS. Decide case by case **before** sizing: SEC-LOG-2 to SEC-LOG-6 change the cost radically.

**Q14 — Activity 5 on the client side.** If the client itself administers the IS holding the health data, this activity falls under its own responsibility and may, in some set-ups, call for its own certification. A decision to document case by case — there is no general rule.

### Ongoing changes, not investigated

- A v2 of the PGSSI-S electronic identification framework has been under consultation since 25 November 2025 (2026-2028 calendar, consultation closed on 24 December 2025). Not approved: the order of 28 March 2022 remains binding. Do not write it as law in force.
- Legifrance references a CNIL deliberation no. 2025-098 of 16 October 2025 giving an opinion on a draft decree amending certain provisions on health-data hosting. A regulatory change in progress, not investigated here, to be checked before any long-term contractual commitment.
- It was not checked whether the ANS has updated its framework or published a FAQ for the 26 September 2026 deadline.

### Two dates not to confuse

| Date | Nature | Reach |
|---|---|---|
| **26 September 2026** | Decree no. 2026-209, art. 1 2° and 3° | **Binding.** R. 1111-9-1 (EU/EEA storage), R. 1111-11 amended (15°, III, 4°, 8°). This is the deadline that commits contracts. |
| December 2026 (announced) | Framework v2.1 | ANS forecast, not covered by an order. A date of **audit practice**, not of law. No additional audit required: checked at the next surveillance or renewal audit. |

A reader who commits on "December 2026" is wrong by three months **and** about the nature of the norm.
