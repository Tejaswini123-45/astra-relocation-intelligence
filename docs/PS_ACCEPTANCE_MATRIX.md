# PS acceptance matrix - SIH26191

**Intelligent Identification of Hazard-Based Red Zones, Carrying Capacity
Assessment, and Immediate Relocation Needs for Vulnerable Habitations.**
Ministry of Home Affairs - NDRF, DM Division.

Each row maps a requirement of the problem statement to the feature that answers
it, the code that implements it, where a judge can see it, the test that proves
it, and when it appears in `docs/DEMO_SCRIPT.md`. Paths are relative to the
repository root; engines are under `apps/api/astra/engines`, routers under
`apps/api/astra/api`, tests under `apps/api/tests`.

## The four scored capabilities

| # | Requirement | Feature | Implementation | UI proof | Test | Demo |
|---|---|---|---|---|---|---|
| C1 | Maps hazard-based red zones | Four hazard sub-models by weighted linear overlay, dominance-preserving composite, spatial red-zone derivation (threshold, clean, buffer, intersect) with per-zone attributes | `hazard.py`, `zones.py`, `service.py`, `risk_router.py` | Risk Explorer: zones, per-hazard layers, click-to-decompose; Command Centre map | `test_hazard_engine.py`, `test_api.py`, `test_golden_config.py` | 1:00 |
| C1 | ...and updates them **in real time** | Event ingest, spatially scoped re-scoring, cascade through every engine, SSE stage stream, plan-invalidation banner | `live.py`, `pipeline.py`, `live_router.py` | Command Centre **Run monsoon escalation**; execution graph; Live Operations; *Plan requires review* | `test_live_engine.py`, `test_live_api.py`, `e2e/live.spec.ts`, `e2e/judge-journey.spec.ts` | 0:25 |
| C2 | Assesses **suitability** of safer sites | Hard gates reported by name: hazard buffer, slope, land cover, flood level, road access | `capacity.py`, `capacity_router.py` | Relocation Sites: gate results per site | `test_capacity_engine.py` | 2:10 |
| C2 | Assesses **carrying capacity** | Usable area from ESA WorldCover (+ RF refinement), per-service capacity against Sphere / PMAY-G norms, effective = min, named bottleneck, ranked marginal interventions | `capacity.py`, `capacity_service.py`, `landcover_ml.py` | Relocation Sites: bottleneck-dominant bars and intervention calculator; Command Centre *What unlocks capacity*; Brief section 4 | `test_capacity_engine.py`, `test_brief.py` | 2:10 |
| C3 | Prioritises **immediate / short-term / medium-term** relocation | Hazard, exposure, vulnerability and history kept separate; priority score; confidence orthogonal; three tiers with override rules; `CAPACITY_BLOCKED` flagged distinctly; phase-specific travel ceilings and capacity shares in the optimiser | `priority.py`, `optimizer.py`, `priority_router.py` | Habitation Priority: tiers, reasoning drawer, rank stability; Plan phases; Command Centre *Action required* | `test_priority_engine.py`, `test_optimizer.py`, `e2e/judge-journey.spec.ts` | 1:35 |
| C4 | **Actionable insights to SDMAs** - allocation | CP-SAT integer allocation under capacity, reliability, travel-time, suitability and household-integrity constraints; greedy fallback labelled; post-solve validation | `optimizer.py`, `optimizer_service.py`, `plan_router.py` | Optimised Plan: assignments, totals, unmet reasons, stranded capacity | `test_optimizer.py` | 2:40 |
| C4 | ...explainable | Counterfactual *why not this site* by real re-solve | `optimizer_service.counterfactual`, `plan_router.py` | Plan: *Why not somewhere else* | `test_optimizer.py`, `e2e/judge-journey.spec.ts` | 2:40 |
| C4 | ...proactive planning under change | Versioned scenarios, full-chain re-run, structured before/after diff | `scenario.py`, `scenario_router.py` | What-If Simulation | `test_scenario_engine.py`, `e2e/simulate.spec.ts`, `e2e/judge-journey.spec.ts` | 3:20 |
| C4 | ...printable Decision Brief | Situation, priorities, capacity, phased actions, route risks, confidence, comparison, assumptions, limitations; frozen with a ledger row | `brief.py`, `brief_router.py` | Decision Brief (`/brief/{id}`), print view | `test_brief.py`, `e2e/judge-journey.spec.ts` | 4:50 |
| C4 | ...audit record and human override | Decision ledger with input hash and versions; approve / override / annotate with required reason; override cost re-solved | `audit.py`, `intelligence_router.py`, `store.py` | Evidence & Audit ledger and override trail; Brief section 11 | `test_intelligence.py` | 4:30 |

## The description, clause by clause

| Clause | Where it is answered |
|---|---|
| "GIS-enabled decision support platform" | MapLibre + deck.gl over a real DEM, OSM network and computed surfaces; every recommendation screen carries the decision-authority line |
| "dynamically identifies and updates multi-hazard Red Zones (areas unsuitable for permanent habitation)" | C1 rows; zones are labelled *ASTRA analytical classification* |
| "assesses the carrying capacity of safer alternative sites" | C2 rows |
| "prioritizes vulnerable habitations for relocation" | C3 row |
| "integrating hazard intensity, population vulnerability, and disaster history" | Priority formula `P = 100 · normalise(wH·H + wE·E + wV·V + wHist·Hist)` in `priority.py`, each term shown with its contribution |
| "evidence-based decisions" | Evidence intake (`evidence.py`) promoted to live observations; validation (`validation.py`, `scripts/backtest.py`) |
| Background: "relocation efforts are largely reactive" | Command Centre cold open; the what-if and live screens plan before the event |

## Credibility and doctrine

| Commitment | Implementation | Proof |
|---|---|---|
| Hazard model validated | `validation.py`, `scripts/backtest.py`, `validation_router.py` | Model & Provenance; `test_validation_engine.py`; `e2e/validation.spec.ts`; CI `backtest.py --check` |
| Weights stress-tested | Monte Carlo +/-20% over 1,000 runs | Model & Provenance; rank-stability chips on Priority |
| Provenance never blended | `data/provenance.py`, registry, class enum | Study Area & Data; Model & Provenance; `test_registry_and_notices.py` |
| Single numeric truth | Generated contracts; shared serialisers | CI contract drift check; `test_brief.py` cross-endpoint equality |
| Deterministic core, LLM narration only | `narration.py` numeric validation, `ask.py` allowlist | Header *AI explanation: Template mode*; `test_intelligence.py` |
| Runs offline with no key | Vendored data; template narration | Local verification with no network dependency |
| Comparative value | `brief.py` comparison rows | Command Centre *Static hazard map vs ASTRA decision mode* |
