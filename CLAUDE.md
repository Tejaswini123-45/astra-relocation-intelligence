# CLAUDE.md — ASTRA · SIH26191 Build Contract

> **Save this file at the repo root before starting.** Claude Code auto-loads it as persistent project context every session. This is the single authoritative brief. Do not re-derive it, do not restate it back, do not re-justify decisions already locked here. Read it once, build.

---

## 0. HOW TO OPERATE (read before anything else)

**Loop for every unit of work:** `INSPECT → DECIDE → IMPLEMENT → SELF-TEST → RED-TEAM → COMMIT → PUSH`

**Token discipline — these are hard rules:**
- Do not print files back into chat. Do not echo diffs longer than 20 lines.
- Do not narrate trivial edits ("now I'll add an import"). Report only at slice boundaries.
- Do not re-explain architecture that is already in this document.
- Do not ask permission for library, naming, colour, or file-layout choices. You have full authority. Make the highest-quality call and move.
- Edit files with targeted diffs. Never regenerate a whole file for a small change.
- Batch related work. One report per vertical slice, in this format only:
  ```
  SLICE n COMPLETE — <capability>
  Built: <3 bullets max>
  Verified: <tests run + result>
  Red-team finding: <the single weakest point + what you did about it>
  Commit: <conventional commit message>
  ```

**Only stop and ask if:** git auth is genuinely unavailable, or something in this document is internally contradictory in a way that changes the PS interpretation. Nothing else.

**Build vertical slices, never horizontal stubs.** Data → engine → API → UI, fully working, one capability at a time. At every point in the build there must be something demoable. Never leave four engines half-stubbed.

---

## 1. THE PROBLEM STATEMENT (authoritative)

**PS ID:** SIH26191
**Title:** Intelligent Identification of Hazard-Based Red Zones, Carrying Capacity Assessment, and Immediate Relocation Needs for Vulnerable Habitations
**Organisation:** Ministry of Home Affairs · **Department:** NDRF, DM Division
**Theme:** Disaster Management · **Category:** Software

**Background (official):** India's disaster-prone regions face recurring hazards such as landslides, floods, coastal erosion, and cloudbursts. Vulnerable habitations often remain in unsafe zones, leading to repeated loss of lives and property. Current relocation efforts are largely reactive, initiated after disasters strike, rather than proactively planned.

**Description (official):** An intelligent, GIS-enabled decision support platform that dynamically identifies and updates multi-hazard Red Zones (areas unsuitable for permanent habitation), assesses the carrying capacity of safer alternative sites, and prioritizes vulnerable habitations for relocation, integrating hazard intensity, population vulnerability, and disaster history to guide evidence-based decisions.

### The four scored capabilities

Every one of these must map to a **visibly working, clickable feature**. A judge will check them line by line. Nothing here may be window dressing.

| # | Official requirement | Must be provable by |
|---|---|---|
| C1 | Maps and updates hazard-based Red Zones **in real time** | A live event ingest that re-triggers scoring and visibly re-renders the map, with a stage-by-stage pipeline trace |
| C2 | Assesses suitability and **carrying capacity** of safer relocation sites | Multi-constraint effective capacity with a named binding bottleneck and a marginal-intervention calculator |
| C3 | Prioritizes habitations for **immediate / short-term / medium-term** relocation | Three genuinely different phase objectives, not three labels on one ranking |
| C4 | **Actionable insights to SDMAs** for proactive planning | Optimised allocation plan + printable Decision Brief + audit record + human override |

### The one-sentence product test

> *ASTRA does not merely show where danger is. It explains who is most at risk, which safer sites can actually absorb them, whether they can safely reach those sites, how to allocate people under real constraints, and how that plan changes when conditions change.*

If a feature does not strengthen that sentence, it does not get built.

---

## 2. NON-NEGOTIABLE DOCTRINE

### 2.1 Deterministic core. ML for perception only. LLM for narration only.

- **Every hazard score, capacity number, priority rank and assignment is produced by a documented, deterministic formula or a solver.** Traceable by a human reading the code, in one hop, from output to inputs.
- **ML has exactly one job:** land-cover derived usable-area estimation for the capacity engine. Nothing else. (See §5.4 — and note that even here the primary path is a real published product, not a model you train.)
- **The LLM has exactly one job:** turning an already-computed structured result into readable prose for an official who is not a GIS analyst. It never computes, never ranks, never selects, never overrides.
- **The system must run end-to-end with the LLM disabled**, falling back to deterministic template narration. If pulling the API key breaks the demo, the architecture is wrong.

State this in the UI, unprompted, in a persistent "How this works" panel:

> *Every red-zone boundary, capacity figure and priority ranking in ASTRA is produced by a documented, auditable formula or a constraint solver. AI is used only to explain those outputs in plain language, never to produce them.*

This sentence is your strongest defence against "so it's a ChatGPT wrapper."

### 2.2 No fabricated authority

ASTRA is **decision support**. It never declares a real place unsafe, never designates a statutory zone, never issues a relocation order.

- Every risk output is labelled **"ASTRA analytical classification"**, never "official red zone".
- Every recommendation screen carries a compact, permanent line: *Decision-support output. Final relocation decisions rest with the SDMA / District Authority.*
- No real, named village is ever presented as classified-unsafe by ASTRA. See §4.1 for how the study area handles this.

### 2.3 No fake anything

Absolutely forbidden, and grounds for reverting the work:
- Buttons that do nothing. Hardcoded "AI responses". Animated pipeline nodes not driven by real backend events. Optimisation output that isn't from the solver. Numbers in the UI that were not computed by the API.
- Claiming "real-time" for a static refresh, "AI prediction" for a deterministic score, "satellite intelligence" without satellite-derived data, or "government integration" without a working connector.

If something is honest but modest, label it honestly and ship it. "ASTRA demo analytical layer" beats faked institutional integration every time.

### 2.4 Single source of numeric truth

**The frontend never computes a displayed metric.** All numbers come from the API and are rendered as received. TypeScript types are **generated from the FastAPI OpenAPI schema** into `packages/contracts` — no hand-written duplicate interfaces. This structurally eliminates the classic failure of one screen saying 590 and another saying 620.

---

## 3. LOCKED TECHNICAL DECISIONS (do not re-litigate)

| Layer | Decision | Why (do not re-argue) |
|---|---|---|
| Frontend | Next.js 14+ (App Router), TypeScript, Tailwind, Radix/shadcn primitives | Fast, deployable, typed |
| Map | **MapLibre GL JS** + **deck.gl** overlay | MapLibre needs no access token, so no key can expire or leak mid-demo, and a self-hosted style works offline. deck.gl handles the heavy hazard/route layers on the GPU **in the user's browser** (this does not violate the no-GPU constraint, which concerns your own training/inference) |
| Basemap | Self-hosted MapLibre style + vendored terrain/hillshade raster tiles for the study bbox only | Demo must survive a dead network |
| Graph/pipeline UI | React Flow, driven by **real SSE events** | Not decoration; see §9 |
| State/data fetching | TanStack Query; Zustand only for map/scenario UI state | |
| Backend | Python 3.11, FastAPI, Pydantic v2 | |
| Geospatial | GeoPandas, Shapely, Rasterio, pyproj, NetworkX | |
| Optimisation | **OR-Tools CP-SAT** (integer assignment), with a deterministic greedy fallback | Solver is the differentiator; fallback is demo insurance |
| Persistence | **Precomputed GeoJSON/GeoParquet on disk for static geospatial layers + SQLite (with SpatiaLite optional) for mutable state** (scenarios, runs, evidence, decisions, audit, overrides) | **PostGIS is explicitly rejected for this prototype.** At 12 habitations / 6 sites / one study bbox it buys nothing and adds a Railway failure mode on demo day. Write a `docs/SCALING.md` note describing the PostGIS migration path — that answers the scaling question without incurring the risk |
| LLM | Provider-agnostic adapter, env-configured, **optional**, with deterministic template fallback | §10 |
| Infra | Docker + docker-compose local; Railway for the public URL | |

**Repo structure:**
```
/apps/web            Next.js command centre
/apps/api            FastAPI + engines
  /engines           hazard.py capacity.py priority.py routes.py optimizer.py scenario.py explain.py validate.py
  /domain            pydantic models, config constants, model registry
  /data              ingestion, provenance registry, fixtures
  /api               routers
/packages/contracts  TS types generated from OpenAPI (build step, committed)
/data                raw/ derived/ fixtures/  (+ provenance.json)
/scripts             seed, validate_fixtures, backtest, export_openapi, gen_types
/docs
CLAUDE.md  README.md  docker-compose.yml  railway.json  .env.example
```

Do not create microservices. Do not add a dependency without a concrete use in the next slice.

---

## 4. STUDY AREA & DATA STRATEGY

### 4.1 Study area (decided)

**Primary scenario: a Himalayan settlement cluster in the Alaknanda valley corridor, Chamoli district, Uttarakhand.**

Rationale: real terrain with genuine slope/rainfall/flood/access interaction, a documented multi-hazard history, and a real relocation policy context — the exact geography this PS was written for.

**Ethical handling — mandatory:**
- **Terrain, rivers, roads, elevation and historical incident points are real open data.**
- **Habitations and candidate relocation sites are synthetic**, placed at terrain-plausible locations, with **fictional names** (`Devgarh Tok`, `Naulkhet`, `Rauligaon`, …) and IDs `H-01…H-12`, `S-01…S-06`.
- The UI states permanently, in the scenario header: *Demonstration scenario. Habitation and site records are synthetic and terrain-calibrated. Not an official hazard designation of any real settlement.*
- **Never** render an ASTRA red-zone classification over a real named village with real population figures.

This is not a limitation to apologise for. It is the correct engineering choice and it preempts the sharpest ethical question a judge can ask.

**Optional second scenario if and only if time remains after §12 Slice 8:** a Kerala coastal-erosion cluster, purely to prove the hazard registry is extensible. Cut it without hesitation.

### 4.2 Provenance classes (enforced in code)

Every layer, table and field carries one of these, as a Pydantic enum, surfaced everywhere it is displayed:

| Class | Meaning |
|---|---|
| `REAL_OPEN` | Real public dataset, unmodified |
| `DERIVED` | Computed by ASTRA from `REAL_OPEN` inputs (slope from DEM, drainage density, kernel density) |
| `SYNTHETIC_CALIBRATED` | Generated by ASTRA, statistically calibrated to the real study area, clearly fictional |
| `DEMO_CONFIG` | An ASTRA-chosen threshold, weight or policy constant, not a government rule |

Every dataset record carries: `source`, `source_url`, `class`, `acquired`, `processing`, `resolution`, `temporal_coverage`, `confidence`, `licence`.

**Any threshold or weight rendered in the UI shows a `DEMO_CONFIG` chip** unless it is traceable to a citable published standard, in which case it shows the citation. Never present an ASTRA constant as law.

### 4.3 Real data to use (fetch once, vendor into `/data/raw`, never fetch at demo time)

- **SRTM 30m / Copernicus DEM** → elevation, slope, aspect, terrain ruggedness, drainage network
- **ESA WorldCover 10m** → land cover (see §5.4: this replaces training a classifier)
- **OpenStreetMap (Overpass extract, cached)** → roads, bridges, streams, settlements context
- **NASA Global Landslide Catalog + published GSI/NRSC landslide inventory points** → historical incident evidence **and the validation set (§7)**
- **Open-Meteo / IMD gridded historical rainfall** → rainfall intensity, return-period proxy
- **Census 2011 / WorldPop** → demographic structure used to *calibrate* the synthetic habitation profiles (documented as calibration, not as the habitation data itself)

Write a **connector interface** per source with a cached local fixture behind it. If a source cannot be reached, the connector serves the fixture and the provenance panel says so. **No external network call is on the critical path of the demo.**

### 4.4 Citable norms — use these, do not invent numbers

Capacity and service constraints must be anchored in published standards, cited inline in code comments and in the UI tooltip:

- **Sphere Handbook (Humanitarian Charter and Minimum Standards)** — water 15 L/person/day; 1 latrine per 20 people; 3.5 m² covered living area per person; 45 m² site area per person; health facility coverage ratios.
- **PMAY-G** plot/dwelling allocation norms for permanent resettlement sizing.
- **BIS / GSI landslide hazard zonation** weighted-overlay methodology as the methodological precedent for §5.1.
- **NDMA guidelines** for relief-camp and shelter provisioning where applicable.

Where a real norm exists, use it and cite it. Where one does not, mark it `DEMO_CONFIG`. That single discipline is worth more in Q&A than any visual polish.

### 4.5 Fixture integrity (blocking)

`scripts/validate_fixtures.py` must run in CI and on API startup, and must **fail hard** on: duplicate IDs, dangling references (route→habitation/site), coordinates outside the study bbox, population exceeding any capacity used to serve it, negative or non-finite values, missing provenance, or any habitation/site/route lacking a required field. The API must refuse to start on invalid fixtures.

---

## 5. THE ENGINES

All weights, thresholds and norms live in **one** versioned config module (`domain/model_config.py`), exposed at `GET /model/config`, and rendered in the UI's transparency panel. There are no magic numbers inside engine logic.

Every engine returns not just a value but **the factor decomposition that produced it**. If a number cannot show its own arithmetic, it does not ship.

### 5.1 Engine 1 — Multi-hazard susceptibility & red zones

Compute a **Hazard Susceptibility Index (HSI)** on a regular grid (~100 m) over the study bbox, per hazard, by **weighted linear overlay of normalised (0–1) factor rasters** — explicitly the GSI/BIS-aligned methodology. Cite that alignment in `DECISION_MODEL.md`.

Four sub-models:
- **Landslide:** slope angle, terrain ruggedness, historical incident kernel density, rainfall intensity (antecedent + return period), land-cover/vegetation proxy, drainage density, distance to fault/lineament if obtainable.
- **Flood:** height above nearest drainage (HAND), distance to drainage, historical inundation overlap, rainfall intensity, infiltration proxy from land cover.
- **Cloudburst / flash flood:** extreme-rainfall event frequency, catchment steepness, drainage confluence density, upstream contributing area. Distinct from riverine flood — this is the Himalayan profile and it matters that you modelled it separately.
- **Coastal erosion:** shoreline retreat rate, elevation above sea level, distance to coastline, surge exposure proxy. Implemented and unit-tested; only exercised if the optional coastal scenario is built.

`HSI_h = Σ_f w_{h,f} · n_f(x)` with `Σ_f w_{h,f} = 1`, scaled to 0–100.

**Composite — preserve dominance, never average it away:**
```
C = max_h(HSI_h) + λ · (second_highest_h(HSI_h))     λ = 0.25 (DEMO_CONFIG), C clipped to 100
```
Always retain and surface the full per-hazard vector plus the dominant hazard. "Why is this red?" must always answer with a *named hazard and its top two factors*, never a single opaque number.

**Red zone derivation is spatial, not a scalar cutoff.** Threshold the composite surface, apply morphological cleaning (remove slivers below a minimum mapping unit), buffer, then intersect with exposure relevance. Output multipolygons with per-polygon attributes: area, dominant hazard, mean/max composite, population intersected, confidence, rule version. Classes: **Critical / Elevated / Watch / Low**, each with documented thresholds shown in the UI.

Clicking any zone returns the full decomposition: score, per-hazard breakdown, ranked factor contributions with their numeric weights, population intersected, confidence, evidence age, rule version.

### 5.2 Engine 2 — Exposure & vulnerability (hazard ≠ consequence)

Keep these three quantities **structurally separate** and show them separately in the UI. This is ASTRA's clearest conceptual advantage and the thing a non-technical judge will most easily grasp.

- **Hazard** `H`: composite HSI sampled over the habitation footprint (kernel mean + max), 0–100.
- **Exposure** `E`: normalised from population, households, and count of critical facilities (school/clinic/anganwadi) within footprint.
- **Vulnerability** `V` (0–1): weighted share of elderly, children under 5, persons with disabilities, medically dependent residents, single-earner/low-income households, plus a structural-typology proxy (kutcha/semi-pucca share). Demographic *proportions* calibrated to Census/SECC district figures; absolute counts are `SYNTHETIC_CALIBRATED`.
- **History** `Hist`: recency-and-severity weighted incident density, `Σ_i severity_i · exp(-Δt_i / τ)` within radius r. Never treated as proof of future hazard; it is one weighted factor and the UI says so.

**Priority score:**
```
P = 100 · normalise( wH·Ĥ + wE·Ê + wV·V + wHist·Ĥist )
```
Defaults: `wH .35, wE .25, wV .25, wHist .15` — all `DEMO_CONFIG`, all live-adjustable in the UI, all shown with their contribution to the final number.

**Confidence is orthogonal to priority and is never multiplied into it.** Confidence is computed separately from data completeness, provenance class mix, evidence recency and spatial resolution, and rendered alongside as an independent badge. The UI must say, explicitly: *Priority 92/100 is a ranking score, not a 92% probability. Evidence confidence: Medium.* Conflating the two is the single most common technical error in this problem space; not making it is a visible sign of rigour.

### 5.3 Engine 3 — Phase tiering (Immediate / Short-term / Medium-term)

Use the PS's exact three-tier language. Tiering is **not** three slices of one ranking — each tier has a different objective function:

- **Immediate:** maximise lives-at-risk moved *now*, constrained to currently viable capacity and routes with reliability above threshold. Hard override rules apply (e.g. any habitation inside a Critical zone with `V` above threshold enters Immediate regardless of composite score — an override, documented and visible as such).
- **Short-term:** relieve the binding bottlenecks identified by Engine 4, expand usable capacity, then reallocate. Objective weights shift toward capacity unlocked per unit of intervention.
- **Medium-term:** full residual demand under strengthened infrastructure assumptions, minimising long-run livelihood disruption and settlement fragmentation.

A habitation that is high-risk but has **no feasible matched capacity** is flagged distinctly — `CAPACITY_BLOCKED` — rather than silently ranked. That state is itself actionable intelligence for an SDMA, and it is exactly the nuance that separates this from a sorted table.

### 5.4 Engine 4 — Site suitability & multi-constraint carrying capacity

**Step 1 — Hard suitability gates (binary, must pass all):** outside all Critical/Elevated zones plus safety buffer; slope below build-safe threshold; not in protected forest / water / wetland land-cover class; above flood return-period level; road-accessible. A gate failure is reported as a named gate, not as a low score.

**Step 2 — Usable area.** Primary path: **ESA WorldCover 10m** real land-cover product, reclassified to buildable/non-buildable, intersected with the site polygon and slope mask. This is real data and requires no model training. **Secondary (the scoped ML component):** a small scikit-learn Random Forest over spectral indices (NDVI/NDBI/NDWI) from an open Sentinel-2 composite for the study bbox, trained on a handful of hand-labelled patches, CPU-only, used as a refinement layer and to demonstrate the perception capability. Report both, with agreement between them as a confidence signal. Never claim a deep-learning segmentation model you did not train.

**Step 3 — Per-service capacity, then the binding constraint.** For each service `s`, `cap_s = supply_s / norm_s` using the §4.4 cited norms:

```
Land       usable_area / (area per person norm)
Shelter    existing units × occupancy norm + constructable units
Water      litres/day available / 15 L per person per day        [Sphere]
Sanitation latrine units × 20                                    [Sphere]
Healthcare facility capacity / population-per-facility norm
Power      kVA available / per-household load norm
Access     route throughput ceiling (from Engine 5)
```

```
Theoretical capacity = cap_land
Effective capacity   = min_s(cap_s)
Bottleneck           = argmin_s(cap_s)
```

**Step 4 — Marginal intervention analysis.** For each service, compute the capacity unlocked per unit of intervention and rank interventions by effective-capacity gain per unit:
> `+2 sanitation blocks → effective capacity 590 → 710 (+120). Next binding constraint becomes water at 710.`

That sentence, rendered live in the UI, is a stronger demo moment than any animation you can build. Make it a first-class feature, not a tooltip.

**Mandatory proactive limitation, on the site panel itself:** *ASTRA narrows the candidate search using terrain, land cover and service data. It does not verify land ownership, tenure or encumbrance — that requires an SDMA field survey.* Saying this before you are asked converts your biggest vulnerability into evidence of rigour.

### 5.5 Engine 5 — Route reliability & survivability

Build a routed graph from the OSM road network with NetworkX. Per segment: length, class-based free-flow speed, bridge/culvert dependency flag, hazard exposure sampled from Engine 1, and a scenario-controlled closure state.

Per route:
- travel time, distance, hazard-intersection count and length
- **reliability** `R = Π_seg (1 − p_fail,seg)` where `p_fail` is derived from segment hazard exposure and bridge dependency, all `DEMO_CONFIG` and shown
- overall route risk, longest hazard exposure, single points of failure

Return **two routes when they differ**: `FASTEST` and `SAFEST` (minimising `time · (1 + α · risk)`), with the tradeoff stated numerically. A route below the reliability threshold makes a site **infeasible for that habitation** in the optimiser, not merely penalised.

### 5.6 Engine 6 — Constrained relocation optimisation (OR-Tools CP-SAT)

The analytical heart. Not a greedy sort dressed up.

**Variables:** `x[h][s][phase]` = integer number of people from habitation `h` assigned to site `s` in `phase`.

**Constraints:** site effective capacity per phase; route reliability above threshold; travel time below phase-specific ceiling; no assignment to a site failing suitability gates; per-scenario policy on partial vs whole-habitation moves; household-integrity (no splitting below a minimum block size, to avoid absurd fragmentation); phase capacity ramp.

**Objective (weighted, configurable, every term exposed in the UI):**
```
minimise  Σ  β1 · unmet_demand_h · priority_h
        + β2 · population · travel_time
        + β3 · population · route_risk
        + β4 · site_overload_penalty
        + β5 · livelihood_disruption            (see below)
        + β6 · fragmentation_penalty
```

**Livelihood disruption is a computed composite, never a hardcoded "5 km rule".** It combines travel time to the original livelihood centre, connectivity class, road reliability, and market/service access at the destination. The UI shows the components. If any prior material implied a fixed kilometre rule, that is superseded here.

**Reliability requirements:**
- Deterministic seed and fixed solver time limit so the demo is reproducible.
- A greedy priority-ordered fallback if the solver hits its limit, clearly labelled in the response as `FALLBACK`.
- Post-solve validation asserting no capacity breach, no infeasible route, no orphan assignment. Failing that assertion is a fatal error, never a silently rendered plan.

**Explanations must come from real re-solves, not from prose.** For "Why not site S-01 for H-04?", re-solve with that assignment forced and report the actual outcome: infeasibility with the named binding constraint, or the objective delta versus the chosen plan. This is a genuine counterfactual, and it is the difference between an explanation a judge accepts and one they poke a hole in.

Output per assignment: source, destination, people, phase, route, travel time, reliability, livelihood disruption, binding constraints, objective contribution, ranked rejected alternatives with the actual reason each was rejected.

### 5.7 Engine 7 — Scenario / what-if recalculation

Scenarios are **first-class, versioned, diffable objects**, not UI toggles.

Perturbations: rainfall intensity multiplier; landslide susceptibility shift; named bridge/road closure; site capacity loss; service upgrade at a site; new hazard evidence ingested; population change; candidate site disabled.

`POST /simulate` runs the full chain and returns a **structured diff against the baseline run**: red-zone area delta, habitations changing tier, priority deltas per habitation, capacity deltas per site, route status changes, assignment changes, phase changes, population newly requiring immediate action.

Render as explicit before → after. Never a silent replace.

### 5.8 Engine 8 — Real-time event ingest (this is capability C1, do not weaken it)

A `POST /events` endpoint accepting rainfall observations, incident reports and field evidence. Ingest triggers **spatially scoped incremental re-scoring** of affected cells only, which cascades through exposure → capacity → routes → optimiser, emitting SSE stage events throughout. The map re-renders visibly, a habitation's tier visibly changes, and a **"Plan requires review"** banner appears with the specific decisions invalidated.

For the demo, a controllable event feed replays a rainfall escalation sequence. This is real streaming ingest with a controlled source, not a fake refresh button, and the code must make that obvious to anyone who reads it.

---

## 6. EXPLAINABILITY & AUDIT

**Every displayed number is inspectable in one click**, returning: value, formula ID and version, inputs with their values, weighted contributions, provenance class per input, confidence, timestamp.

Three explanation surfaces, all backed by structured data:
- **Why this habitation first** — hazard / exposure / vulnerability / history contributions with numbers, feasible capacity status, phase decision, overriding rules applied.
- **Why this site is the bottleneck** — per-service capacities, the argmin, marginal interventions ranked.
- **Why not this site** — the real counterfactual re-solve from §5.6.

**Audit ledger.** Every run writes: `decision_id, scenario_id, timestamp, engine_version, model_config_version, source_layer_ids, input_summary_hash, score_components, constraint_status, solver_status, objective_value, confidence, human_override, override_reason, notes`. Surfaced in a Decision Audit panel and referenced by ID on the printed Decision Brief.

**Human in the loop.** Approve / Override / Annotate / Recalculate on every plan. An override requires a reason, is recorded, and the UI shows the consequence of the override (capacity and risk deltas versus the computed plan). No screen may imply ASTRA itself designates zones or orders relocation.

---

## 7. VALIDATION & SENSITIVITY — THE CREDIBILITY LAYER

**This section is what separates a top-tier submission from a competent one. Neither a pretty map nor a solver earns trust on its own. Evidence that you tested your own model does.** Build both, surface both in a **Model Validation** panel.

### 7.1 Back-test the hazard model against historical incidents

Hold out the historical landslide/flood inventory points. Compute, over the study area:
- **ROC-AUC** of the composite HSI as a predictor of known past incident locations, against sampled non-incident background points
- **Success-rate curve**: what fraction of known historical incidents fall within the top X% of ASTRA's susceptibility surface (the standard validation used in published landslide-susceptibility literature)
- Per-hazard breakdown

**Report the number honestly, whatever it is.** A stated AUC of 0.78 with an explained methodology is enormously more credible than an unvalidated map. Add a plain caveat that inventory completeness and spatial bias limit the ceiling of this validation.

`scripts/backtest.py` produces this reproducibly and it runs in CI.

### 7.2 Weight sensitivity / rank stability

Weights are expert-chosen, therefore subjective, therefore attackable. Answer it before it is asked: Monte Carlo perturb every weight by ±20% over ~1000 runs and report:
- Spearman rank correlation of habitation priority against baseline
- % of runs in which the top-5 set is unchanged
- Which habitations are rank-stable and which are weight-sensitive (flagged in the UI)

> *"Our top three priority habitations remain top three in 96% of runs under ±20% weight perturbation"* is a sentence that ends an entire line of hostile questioning.

### 7.3 Confidence surface

Per zone and per habitation, a confidence value from data completeness, provenance mix, evidence recency and resolution. Rendered as a distinct visual treatment (hatching/opacity), never as false uniform certainty. Low-confidence areas must look different on the map.

---

## 8. UI / UX — THIS IS WHERE THE ROOM IS WON OR LOST

Judges are largely evaluators and administrators, not GIS engineers. An accurate flat map with a legend loses to a clearer system with better presentation. Equally, a beautiful shell over nothing dies in Q&A. Build both.

### 8.1 The first 20 seconds (specified, not left to taste)

Do **not** open on a login, a splash, a navbar, a KPI grid, a table, or a generic dashboard.

Open on **stakes, then live system**, in one continuous motion:

1. **~6s cold open.** Near-black surface. Real terrain at low luminance. A restrained factual line about recurring displacement from hazard-prone habitations and the reactive nature of current relocation, with a cited real event context. No stock imagery, no illustration, no hero copy, no emoji.
2. **~4s transition.** The camera pushes into the Alaknanda study corridor as layers resolve in sequence: terrain → drainage → roads → habitations → candidate sites. The transition *is* the system loading real layers, not a video.
3. **Settle into the Command Centre**, already showing an active scenario, a live decision state, and one unmissable primary action: **RUN MONSOON ESCALATION**.

Within 20 seconds a judge must know: what is being decided, for whom, and what happens next.

### 8.2 Visual identity

Emergency operations centre meets scientific instrumentation. Calm authority, restrained urgency, high information density without clutter.

- **Palette:** deep charcoal/navy foundation; off-white content surfaces where data density demands it. Colour carries semantics only: a designed severity gradient for hazard (not traffic-light clichés), red reserved exclusively for critical risk, amber for warning, teal for safe/positive, muted grey for system-neutral. Never colour alone as the sole distinction.
- **Typography:** one precise UI sans (Inter/Geist) plus a tabular-figure mono for all numerics. Numbers must align in columns. Tabular figures are a small detail that reads instantly as professional software.
- **Motion:** purposeful only — route reveal, zone transition, scenario recompute, panel entry. Nothing loops. Nothing pulses without meaning.
- **A bespoke ASTRA wordmark and SVG mark.** Subtle contour/grid motif. Considered empty states.
- **Banned:** glassmorphism excess, neon cyberpunk, emoji, gradient meshes, sparkle/"AI magic" effects, "AI-Powered" badges, stock illustration, giant welcome text, default Bootstrap/Material/Tailwind-template look, rounded-rectangle-everything.

**Product positioning line:** *ASTRA — Proactive Settlement Risk & Relocation Intelligence.*

### 8.3 Layout

Top: identity, active scenario, data freshness, system status, decision state.
Left: compact operational navigation.
Centre: the dominant geospatial command surface.
Right: contextual Decision Intelligence panel.
Bottom: execution graph / evidence / timeline, contextual per screen.

The map is the dominant object. Not every screen needs every region.

### 8.4 Screens (build in this order; the last two are P1)

1. **Command Centre** — map-dominant. Current situation, action required, ASTRA recommendation, one primary action. No KPI card wall.
2. **Risk Explorer** — layer control (hazard per type, composite, exposure, vulnerability, red zones, history, habitations, sites, roads), factor decomposition on click, confidence overlay, blend/opacity.
3. **Habitation Priority** — ranked decision list bound to the map, not a bare table. Row select → map highlight → full reasoning drawer. Phase tier, `CAPACITY_BLOCKED` flag, rank-stability indicator.
4. **Relocation Sites** — capacity diagnostics where the **bottleneck is visually dominant**. Theoretical vs effective vs used vs remaining. Per-service bars. Marginal intervention calculator. Suitability gate results.
5. **Optimised Plan** — assignment lines on the map with selective emphasis and clustering (never 100 raw lines). Totals, capacity consumed, unresolved demand, travel burden, risk reduction versus do-nothing. Per-assignment explanation. Approve / Override.
6. **What-If Simulation** — controls left, map centre, impact comparison right, before/after table bottom. Explicit deltas.
7. **Evidence & Audit** (P1) — evidence intake (text, photo, geolocation, timestamp, analyst note), structured extraction, decision ledger, override trail.
8. **Model & Provenance** (P1) — live weights and thresholds, formula documentation, validation results (§7), per-layer provenance table, limitations, system status.

Plus **Decision Brief**: a clean printable report — scenario, current risk, top priority habitations, available effective capacity, recommended allocations, immediate / short-term / medium-term actions, key bottlenecks, route risks, data confidence, assumptions, limitations, decision audit ID.

### 8.5 Demo Mode

A scripted, unattended, sub-90-second walkthrough of all four engines, ending on the what-if replan. An actual UI sequence driven by the real API, not a slideshow. Escapable at any point into manual control.

### 8.6 Comparative value panel

A compact **Static Hazard Map vs ASTRA Decision Mode** comparison, showing what the added layers (exposure, vulnerability, capacity, route, optimisation, phasing, what-if) change about the resulting action. This answers "how is this different from existing GSI susceptibility maps" visually, in three seconds, without you having to talk over it.

### 8.7 Non-negotiable UI hygiene

Keyboard-accessible controls, visible focus states, WCAG AA contrast, semantic buttons, tablet layout intact with no horizontal overflow, no broken states, lazy-loaded secondary screens. Map interaction stays responsive: simplified geometry, precomputed derived layers, server-side spatial queries, no giant rasters shipped to the browser.

---

## 9. EXECUTION GRAPH (real, or not at all)

React Flow graph of the pipeline: `INGEST → HAZARD → EXPOSURE+VULNERABILITY → SITE CAPACITY → ROUTE RELIABILITY → OPTIMISATION → DECISION BRIEF`.

**Driven by a real SSE event stream** from the backend (`GET /runs/{id}/stream`), emitting `stage_started`, `stage_progress`, `stage_completed`, `warning`, `stage_failed` with actual payloads: rows processed, computed values, elapsed milliseconds. Nodes activate as stages truly execute; outputs and warnings appear as they are produced.

If the backend does not emit it, the graph does not show it. Hardcoded animation here is a fabricated feature under §2.3 and will be caught by any architect in the room.

It should read as an operational intelligence pipeline, not a developer debug view. A judge should be able to understand the architecture from it without reading code.

---

## 10. LLM LAYER (narrow, optional, safe)

**Permitted uses only:** narrating an already-computed decision into an SDMA-readable advisory; summarising and classifying uploaded field evidence into a structured schema; mapping a natural-language question to a **whitelisted structured query** over existing state.

**Hard requirements:**
- Prompts receive **structured computed results only**. The model never receives raw data and is never asked to score, rank, or select.
- All output that contains numbers is **validated against the source structured object** before rendering. A mismatch is dropped, never displayed.
- **Deterministic template fallback** when no key is configured, so the full demo runs offline. UI status shows `AI EXPLANATION: Template mode` versus `Connected`.
- The natural-language interface maps intents to a fixed allowlist of API calls. **No free-form SQL, no filesystem access, no code execution, no shell.** Reject anything unmapped.
- The chatbot is an interface layer, never the hero. The product must be fully understandable without ever typing into it.

---

## 11. API, TESTING, CI, DEPLOYMENT, SECURITY

**API** (Pydantic-typed, OpenAPI-exported, TS types generated):
```
GET  /health                    GET  /model/config
GET  /scenarios  /scenarios/{id}
GET  /layers  /provenance
GET  /habitations  /sites  /routes
GET  /risk/habitations  /risk/zones
GET  /capacity/sites  /capacity/sites/{id}/interventions
POST /routes/evaluate
POST /optimize                  POST /simulate
POST /events                    POST /evidence
GET  /runs/{id}  /runs/{id}/stream (SSE)
GET  /decisions/{id}  /audit/{id}
POST /decisions/{id}/override   POST /brief
GET  /validation                (backtest + sensitivity results)
```

**Tests (minimum, all must pass before any milestone commit):**
- Engines: hazard factor normalisation and composite; vulnerability weighting; capacity bottleneck selection including tie cases; marginal intervention; route reliability including a closure; priority tiering including override rules; optimiser constraint satisfaction; optimiser infeasibility handling; scenario diff correctness.
- Golden-file tests pinning known inputs to known outputs, so a refactor cannot silently change a demo number.
- Data: fixture validation, spatial sanity, ID referential integrity, capacity-vs-assignment invariants.
- **One Playwright E2E covering the exact judge journey:** load → run scenario → inspect top habitation → inspect site bottleneck → run what-if with bridge closure → assert assignments actually changed → open decision brief.

**CI (GitHub Actions):** lint, typecheck (both sides), backend tests, fixture validation, backtest script, frontend build, Playwright E2E. Nothing decorative.

**Deployment:** Dockerfiles, docker-compose for local, `railway.json`, health endpoint, env vars, seed/migrate step. **Target a live public Railway URL by the end of the first build day** — a working link judges can open before the finale materially improves shortlisting odds. The demo must also run fully offline from a laptop with seeded data.

**Security:** `.gitignore` secrets from commit one, `.env.example` only, no keys in git, validate all external and LLM input, no arbitrary execution paths, no unrestricted queries.

---

## 12. BUILD ORDER — VERTICAL SLICES, ONE COMMIT EACH

Repo: `https://github.com/AKSINGH-0704/SIH-26-Astra.git` · branch `main`

**Before writing code:** `git ls-remote` the repo. If it has content, integrate, do not overwrite. Never force-push over existing work. If there is a real conflict with this plan, surface it and stop.

**Commit protocol:** Conventional Commits. One commit per completed, working slice. Never a half-working intermediate state. Never two unrelated slices in one commit. Never a no-op or filler commit. Messages describe the capability added, not the files touched. Before each push: run tests, run fixture validation, review the diff, confirm no secrets. The commit history is part of the deliverable and will be read as a build log.

| # | Slice | Commit |
|---|---|---|
| 1 | Monorepo scaffold, domain models, config module, provenance registry, contracts pipeline, Docker, Railway config, CI skeleton | `chore: scaffold ASTRA monorepo, domain model and deployment baseline` |
| 2 | Real data ingest for the study bbox, derived rasters (slope, HAND, drainage, ruggedness), synthetic habitation/site generation, fixture validator | `feat(data): establish Chamoli study area, provenance registry and validated demo dataset` |
| 3 | **Engine 1 + map, end to end.** Four hazard sub-models, composite, red-zone polygonisation, `/risk/*`, MapLibre+deck.gl surface, factor-decomposition drawer | `feat(hazard): multi-hazard susceptibility engine and analytical red-zone mapping` |
| 4 | Exposure/vulnerability/history, priority scoring, three-tier phasing with override rules, confidence model, Priority screen | `feat(priority): exposure, vulnerability and phased relocation prioritisation` |
| 5 | Suitability gates, land-cover usable area, per-service capacity, bottleneck, marginal interventions, Sites screen | `feat(capacity): multi-constraint carrying-capacity engine with bottleneck analysis` |
| 6 | Route graph, reliability, fastest vs safest, closure handling, route layer | `feat(routes): route reliability and survivability analysis` |
| 7 | CP-SAT optimiser, constraints, fallback, post-solve validation, counterfactual re-solve explanations, Plan screen | `feat(optimizer): constrained relocation optimisation with counterfactual explanations` |
| 8 | Scenario objects, `/simulate`, structured diff, What-If screen with before/after | `feat(scenarios): what-if recalculation engine with structured impact diff` |
| 9 | Event ingest, incremental re-scoring, SSE run stream, React Flow execution graph, plan-invalidation banner | `feat(realtime): live event ingest, incremental re-scoring and execution pipeline` |
| 10 | Backtest (ROC-AUC, success-rate curve), weight sensitivity, confidence surface, Model & Provenance screen | `feat(validation): hazard model back-testing and weight sensitivity analysis` |
| 11 | Evidence intake, audit ledger, override workflow, LLM adapter with template fallback, whitelisted NL query | `feat(intelligence): evidence intake, audit ledger and scoped explanation layer` |
| 12 | Cold open, Demo Mode, comparative panel, Decision Brief print view, visual pass | `feat(demo): judge-ready presentation flow, decision brief and demo mode` |
| 13 | Full docs, acceptance matrix, self-audit, E2E, Railway live, final polish | `docs: architecture, provenance, decision model, acceptance matrix and self-audit` |

**If time runs short, cut in this order (from the bottom up):** second scenario, coastal UI, NL query, evidence intake, execution graph. **Never** cut: the analytical core, the optimiser, the what-if, explainability, the validation panel, or the visual quality of the Command Centre.

---

## 13. SELF-AUDIT GATE — RUN AT EVERY SLICE, RECORD IN `docs/SELF_AUDIT.md`

Do not declare a slice done until you have answered all of these in writing. If any answer is "no" or "not sure", that is the next thing you fix, not a footnote.

1. Is any part of this actually an LLM guessing, dressed as a computed score?
2. Can every number on screen be traced by a human, in one hop, to a documented formula and its inputs?
3. Is anything animated or displayed that is not backed by a real backend event or value?
4. Is any `DEMO_CONFIG` constant presented as if it were a government rule, law or statutory designation?
5. Does the provenance panel honestly distinguish real / derived / synthetic per layer, without blending?
6. Are the same figures identical across every screen that shows them?
7. Does the opening 20 seconds avoid looking like a generic dashboard?
8. Does every recommendation screen make it unambiguous that the SDMA decides, not ASTRA?
9. Does the demo run start to finish with the network off and no LLM key?
10. Can the optimiser output an assignment that breaches capacity, an unusable route, or an unsuitable site? (Prove not, with a test.)
11. Is there any feature that looks impressive but changes no decision? Delete it.
12. Would this survive a GIS-literate judge saying "walk me through exactly how you got this number", live?

### Final gate — review as six people

**SIH evaluator:** is this genuinely SIH26191, or a neighbouring problem?
**GIS expert:** are the spatial computations coherent, validated and inspectable?
**Disaster-management officer:** would an SDMA actually know what to do with this screen?
**Software architect:** does it really compute, or is the UI hiding constants?
**AI reviewer:** where is AI genuinely useful and where is it being abused?
**Hostile judge:** what can I attack in 60 seconds?

Fix every serious finding before declaring completion.

---

## 14. ANTICIPATED JUDGE QUESTIONS — THE ANSWER MUST BE ON SCREEN

Not in the pitch script. On screen, already visible.

| Question | What it points at |
|---|---|
| How is this different from existing GSI susceptibility maps? | The comparative panel (§8.6): they map hazard; ASTRA adds exposure, vulnerability, capacity matching, route feasibility, optimisation and phasing |
| Why should I trust your hazard score over a black box? | It isn't one — walk to the live formula, weights and factor contributions (§8.4.8) |
| Is your model any good? | Back-test ROC-AUC and success-rate curve (§7.1) |
| Your weights are arbitrary. | Sensitivity and rank stability (§7.2) |
| How do you validate capacity without a ground survey? | The proactive limitation on the site panel (§5.4) |
| What about regions with no data? | Provenance panel plus per-zone confidence surface (§7.3) |
| Who decides to relocate people, your AI? | Human-in-the-loop framing on every recommendation screen (§6) |
| Is "real time" real? | The event feed, incremental re-scoring and live SSE pipeline (§5.8, §9) |
| Why did it choose that site? | Counterfactual re-solve, not narration (§5.6) |
| Does it scale? | `docs/SCALING.md`, PostGIS migration path, complexity notes |

---

## 15. DOCUMENTATION (write at Slice 13, not before)

`README.md` (problem, solution, architecture, live URL, screenshots, local setup, demo mode, features, provenance, limitations), `docs/ARCHITECTURE.md` (with Mermaid diagram), `docs/DECISION_MODEL.md` (every formula, weight, threshold, citation), `docs/DATA_PROVENANCE.md`, `docs/VALIDATION.md`, `docs/DEMO_SCRIPT.md`, `docs/LIMITATIONS.md`, `docs/SELF_AUDIT.md`, `docs/SCALING.md`, `docs/PS_ACCEPTANCE_MATRIX.md`.

**`docs/PS_ACCEPTANCE_MATRIX.md` maps every PS requirement to: feature → implementation file → UI proof → test → demo timestamp.** A judge should be able to verify in 60 seconds that ASTRA answers the actual PS and not an adjacent problem.

---

## 16. DEMO SCRIPT (target 5 minutes, everything one or two clicks away)

```
0:00  Cold open: stakes, then the live study area resolves
0:25  Baseline scenario. Run the pipeline. Execution graph fires on real events
1:00  Red zones appear. Click one: dominant hazard, factor contributions, confidence
1:35  Top priority habitation: hazard vs exposure vs vulnerability, why it is #1,
      priority 92 is a rank not a probability, confidence stated separately
2:10  Candidate site: effective capacity 590, bottleneck sanitation,
      +2 units → 710, next constraint becomes water
2:40  Run optimisation. Assignments render. Open one: route, reliability,
      why not S-01 (real counterfactual re-solve)
3:20  Trigger bridge closure + rainfall escalation. Map, routes, priorities,
      assignments all change. Before → after diff
4:05  Model & Provenance: weights, back-test AUC, rank stability, per-layer provenance
4:30  Override an assignment with a reason. Audit record written
4:50  Decision Brief: immediate / short-term / medium-term actions, audit ID
```

---

## 17. FINAL ACCEPTANCE

Do not declare complete until, from a clean clone:

frontend builds · backend starts · fixtures validate · seed loads · all tests pass · production build passes · E2E passes · Railway URL live · full demo flow works offline with no LLM key · map renders at target frame rate · optimiser returns valid, capacity-respecting assignments · what-if changes real outputs · events appear in the audit ledger · every number is consistent across every screen · no secrets committed · commit history reads as a coherent build log.

**Governing preference, in every trade-off:**

> **truthful + explainable + working + beautiful** over **flashy + vague + fabricated + fragile**

Build ASTRA as though a GIS expert, a disaster-management officer, a software architect, an AI reviewer and a hostile evaluator will all interrogate it inside the same five minutes. Because they will.

**Begin at Slice 1. Do not stop at scaffolding. Do not stop at a UI shell. Do not stop at mocked data.**
