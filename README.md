# ASTRA

**Proactive Settlement Risk & Relocation Intelligence**
Smart India Hackathon 2026 &middot; PS **SIH26191** &middot; Ministry of Home Affairs / NDRF, DM Division

ASTRA does not merely show where danger is. It explains who is most at risk,
which safer sites can actually absorb them, whether they can safely reach those
sites, how to allocate people under real constraints, and how that plan changes
when conditions change.

> Every red-zone boundary, capacity figure and priority ranking in ASTRA is
> produced by a documented, auditable formula or a constraint solver. AI is used
> only to explain those outputs in plain language, never to produce them.

> Decision-support output. Final relocation decisions rest with the SDMA /
> District Authority. Habitation and candidate-site records in the demonstration
> scenario are synthetic and terrain-calibrated, and are not an official hazard
> designation of any real settlement.

## Documentation

[Architecture](docs/ARCHITECTURE.md) · [Decision model](docs/DECISION_MODEL.md) ·
[Data provenance](docs/DATA_PROVENANCE.md) · [Validation](docs/VALIDATION.md) ·
[Demo script](docs/DEMO_SCRIPT.md) · [Limitations](docs/LIMITATIONS.md) ·
[PS acceptance matrix](docs/PS_ACCEPTANCE_MATRIX.md) · [Scaling](docs/SCALING.md) ·
[Self-audit](docs/SELF_AUDIT.md)

## What it does

| PS capability | Where to see it |
|---|---|
| **C1** Maps and updates hazard-based red zones in real time | Risk Explorer; Command Centre **Run monsoon escalation** with the SSE-driven execution graph; Live Operations |
| **C2** Assesses suitability and carrying capacity of safer sites | Relocation Sites: named gates, per-service capacity, the binding bottleneck and the marginal-intervention calculator |
| **C3** Prioritises immediate / short-term / medium-term relocation | Habitation Priority: separate hazard, exposure, vulnerability and history; three tiers with override rules; `CAPACITY_BLOCKED` |
| **C4** Actionable insights to SDMAs | Optimised Plan (CP-SAT, counterfactual why-not), What-If Simulation, Decision Brief with audit ID, Evidence & Audit with human override |

The app opens on the **Command Centre**: a short cold open with the real event
context, then the corridor resolving layer by layer into the current situation,
the action required, ASTRA's recommendation and a *Static hazard map vs ASTRA
decision mode* comparison. **Demo mode** there runs an unattended walkthrough of
every screen driven by the real API (about 85 seconds; Escape stops it).
**Generate Decision Brief** writes a ledger row and opens a printable brief.

Validation, as the Model & Provenance screen reports it: cross-validated ROC-AUC
0.79 (95% CI 0.68-0.88) against 18 recorded incidents; median Spearman 0.993 and
an unchanged top 5 in 85% of 1,000 runs under +/-20% weight perturbation.

### Priority and phasing

Hazard is not consequence, and ASTRA keeps them apart:

    P = 100 x ( 0.35*hazard + 0.25*exposure + 0.25*vulnerability + 0.15*history )

Each component is computed and reported separately with its own factor
decomposition, and each is scaled against a stated reference so that a declared
weight carries what it says it carries. Phase tiering applies documented
thresholds plus override rules - a vulnerable or populous settlement standing on
Critical ground is escalated to Immediate regardless of score, and the rule that
escalated it is named on screen. Evidence confidence is computed separately and
is deliberately lower than the confidence in the terrain, because the demographic
composition is an assumption while the terrain is measured.

### The hazard model

Three sub-models - landslide, flood and cloudburst/flash-flood - score a
387 x 432 grid of 100 m cells by weighted linear overlay of normalised factors,
the methodology used in published landslide hazard zonation:

    HSI_h = 100 x sum_f( w_hf x n_f(x) ),   sum_f w_hf = 1
    C     = min(100, max_h(HSI_h) + 0.25 x second_highest_h(HSI_h))

The composite preserves dominance instead of averaging it away, so a cell
exposed to two hazards at once scores higher than either alone. The per-hazard
vector, the dominant hazard and every factor contribution survive to the API:
clicking any point on the map returns the measured value, normalised value,
weight and contribution of every factor, with the formula and config versions
that produced them. Evidence confidence is computed separately and never
multiplied into the score. A coastal-erosion sub-model is implemented and
unit-tested; it is scored only where coastal inputs exist, which they do not in
a Himalayan corridor.

### Data in the corridor

Six real open datasets are vendored under `data/raw` for the Alaknanda valley,
Chamoli district: Copernicus DEM GLO-30, ESA WorldCover 10 m, the OpenStreetMap
road and waterway network, the NASA Global Landslide Catalog, ERA5 daily
precipitation and the WorldPop population surface. Fourteen terrain and hydrology
surfaces are computed from them offline - slope, ruggedness, filled DEM, D8 flow
accumulation, channels, height above nearest drainage, drainage density and
distance, catchment slope, hillshade, land-cover reclassification and road
distance.

Twelve habitations and six candidate relocation sites are **synthetic and
fictional**, placed and sized from those real surfaces. Their demographic
composition is an ASTRA assumption, stated as one on screen and in the model
configuration - not a census value.

The interface never renders a layer or a screen before the engine behind it
exists: unbuilt layers are listed and disabled, unbuilt screens are not
navigable.

## Run it locally

Requires Python 3.11+ and Node 20+.

```bash
# One-time data acquisition (the only step that touches the network)
python scripts/ingest.py
python scripts/build_derived.py
python scripts/seed_fixtures.py
python scripts/build_hazard.py

# Backend
python -m venv .venv
.venv/Scripts/python -m pip install -e "apps/api[dev]"     # Windows
# source .venv/bin/activate && pip install -e "apps/api[dev]"   # macOS / Linux
python scripts/validate_fixtures.py                        # integrity gate
uvicorn astra.main:app --app-dir apps/api --port 8000

# Frontend (second terminal)
npm install
npm run contracts        # regenerate TS types from the OpenAPI schema
npm run build:web && npm run start --workspace @astra/web   # http://localhost:3000
# or, for development: npm run dev:web
```

The web client talks to `http://127.0.0.1:8000` unless `NEXT_PUBLIC_ASTRA_API_URL`
is set at build time. End-to-end tests run against both services:
`ASTRA_WEB_URL=http://localhost:3000 npx playwright test`.

Or the whole stack:

```bash
docker compose up --build
```

No external network call is on the critical path: the API reads only local
files, and the LLM narration layer is optional - with no key configured the
system reports `template` mode and runs identically.

## Tests and checks

```bash
cd apps/api && pytest              # engine, contract and integrity tests
ruff check apps/api scripts        # lint
python scripts/validate_fixtures.py
npm run typecheck:web && npm run build:web
```

CI runs all of the above, and fails if the committed OpenAPI schema or the
generated TypeScript types have drifted from the code.

## Repository layout

```
apps/api             FastAPI service
  astra/domain       Pydantic models, versioned model config, formula registry, notices
  astra/data         Study area, provenance registry, fixtures, integrity gate
  astra/engines      Hazard, priority, capacity, routes, optimiser, scenarios, live
                     pipeline, validation, evidence, audit, narration, ask, brief
  astra/api          Routers and response schemas
apps/web             Next.js command centre
packages/contracts   TypeScript types generated from the OpenAPI schema (committed)
data                 raw/ derived/ fixtures/ and the provenance registry
scripts              Ingest, derived surfaces, seeding, integrity gate, back-test, OpenAPI export
e2e                  Playwright tests against the real API, including the judge journey
docs                 Architecture, decision model, provenance, validation, demo script,
                     limitations, PS acceptance matrix, scaling, self-audit
```

## Design commitments

- **Deterministic core.** Every score, capacity figure and assignment comes from
  a documented formula or a solver. Machine learning is scoped to land-cover
  derived usable area; the LLM is scoped to narration.
- **Single source of numeric truth.** The frontend computes no displayed metric.
  TypeScript types are generated from the API schema, never hand-written.
- **Honest provenance.** Every dataset and constant declares whether it is real
  open data, an ASTRA derivation, calibrated synthetic data, or an ASTRA demo
  constant - and the interface shows that class next to the value.
- **No fabricated authority.** ASTRA produces analytical classifications. It does
  not designate statutory zones and does not issue relocation orders.
