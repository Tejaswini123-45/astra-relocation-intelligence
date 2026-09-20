# Demo script

Five minutes, every step one or two clicks away. Figures are the baseline at model
config `1.10.0` / engine `0.6.0`; the screen always shows what the API computes,
so if the configuration changes, read the screen rather than this page.

Before starting: API on `:8000`, web on `:3000`, network optional, no LLM key
needed. If a previous run left live observations, press **Reset to baseline** on
the Command Centre.

For an unattended walkthrough, press **Demo mode** on the Command Centre. It
visits every screen, asks the real API for each caption, runs a What-If through
the screen's own controls, and takes about 85 seconds. Escape stops it.

| Time | Screen | Do | Say |
|---|---|---|---|
| 0:00 | Command Centre | Open `http://localhost:3000/`. The cold open plays (6 s), then the corridor resolves layer by layer | Chamoli, February 2021, more than 200 dead or missing. Relocation is still reactive. The layers resolving are the real data loading: terrain, roads, zones, habitations, sites, planned movements |
| 0:25 | Command Centre | Press **Run monsoon escalation** | Real observations posted to the ingest endpoint. The execution graph lights only as each stage actually runs. Watch critical ground, the immediate tier and the plan move; a *Plan requires review* banner names what was invalidated |
| 1:00 | Risk Explorer | Click a red zone, then a cell | Dominant hazard, per-hazard breakdown, factor contributions with their weights, confidence, rule version. This is an ASTRA analytical classification, not a statutory zone |
| 1:35 | Habitation Priority | Select H-01 Devgarh Tok (priority 61.1) | Hazard, exposure, vulnerability and history are separate, each with its contribution. Priority is a rank, not a probability; confidence is a separate badge. Tier rules and overrides are named |
| 2:10 | Relocation Sites | Select S-06 | Effective capacity 1,200 with healthcare binding. +0.25 facility units of healthcare raises it to 1,380, and the next constraint becomes sanitation. ASTRA does not verify land tenure - that needs a field survey |
| 2:40 | Optimised Plan | Select a movement, then a site under *Why not somewhere else* | CP-SAT, OPTIMAL: 1,144 of 2,519 placed across 2 sites. The why-not is a real re-solve with that assignment forced |
| 3:20 | What-If | **Monsoon escalation** and **Bridge down**, then run | The bridge the plan leans on most is closed. Zones, tiers, routes and assignments change; the diff is before → after, never a silent replace |
| 4:05 | Model & Provenance | Scroll to validation | Cross-validated AUC 0.79 (0.68-0.88) on 18 incidents, with the bias of the inventory stated. Ranking holds median Spearman 0.993 under +/-20% weight perturbation. Per-layer provenance: real, derived, synthetic, demo constant |
| 4:30 | Evidence & Audit | Record the current plan, then override with a reason | The override's cost is re-solved and written beside the reason. Nothing is silently applied |
| 4:50 | Command Centre → Decision Brief | **Generate Decision Brief** | Immediate, short-term and medium-term actions, bottlenecks, route risks, confidence, assumptions and limitations, frozen under a brief ID with its audit decision ID and input hash. The SDMA decides |

## Hostile questions, and where the answer already is

| Question | On screen |
|---|---|
| How is this different from a GSI susceptibility map? | Command Centre, *Static hazard map vs ASTRA decision mode* |
| Why trust your hazard score? | Risk Explorer cell decomposition; Model & Provenance formulas and weights |
| Is the model any good? | Model & Provenance back-test |
| Your weights are arbitrary. | Model & Provenance sensitivity; weight-sensitive flags on Priority |
| Capacity without a ground survey? | The tenure limitation on every site panel |
| Regions with no data? | Confidence hatch on the Risk Explorer; provenance table |
| Who decides, your AI? | Decision-authority line on every screen and on the brief; the override workflow |
| Is "real time" real? | Command Centre escalation and the SSE-driven execution graph |
| Why that site? | Plan why-not re-solve |
| Does it scale? | `docs/SCALING.md` |
