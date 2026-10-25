# Gate log — <project>

Track: **non-HDS** | **HDS** (zone: `par` | `parhds` | `rbxhds` | `grahds`)
Contact: <name, role> | none (autonomous agent mode: recommended option taken)

One line per decision. An assumption made for lack of an answer is **marked
as such** and stays open until the client confirms it.

| Gate | Decision presented | Option taken | Assumption to confirm? | What it commits | Date and time |
|---|---|---|---|---|---|
| Phase 0 | HDS / non-HDS track, zone | | yes / no | zone and add-ons of the whole mission | |
| Phase 0 | Downtime window, data volume, operations, Auth settings | | | cold or hot cutover; batches or not | |
| GATE 1 | Full decision table (`decisions.md`) | | | components ported, rewritten, dropped | |
| GATE 2 | Target and budget (`cutover.md`): plans, flavors, zone, encryption | | | monthly cost, compliance | |
| GATE 3 | Acceptance (`acceptance.md`): source = target matrices, fingerprints | | | sign-off of the migration | |

## Points still open

- <assumption> — to confirm by <who>, before <which step>
