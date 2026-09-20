# Self-audit log

One entry per completed slice, answering the gate in CLAUDE.md section 13. An
answer of "no" or "not sure" is the next thing fixed, not a footnote.

---

## Slice 1 - monorepo scaffold, domain model, config, provenance, deployment baseline

**Date:** 2026-09-09
**Commit:** `chore: scaffold ASTRA monorepo, domain model and deployment baseline`

### What actually works end to end

The API serves the model configuration, the formula registry, the provenance
registry, the layer catalogue, the fixture integrity gate result and the baseline
scenario. The web app renders all of it live from the API, with generated
TypeScript types. Nothing on that screen is typed into the frontend by hand.

### Gate answers

| # | Question | Answer |
|---|---|---|
| 1 | Is any part of this an LLM guessing, dressed as a computed score? | No. There is no LLM call in the codebase yet, and the LLM adapter, when it lands, is narration only. `/health` reports `llm_mode: template` because no key is configured, and everything works. |
| 2 | Can every number on screen be traced in one hop to a documented formula and its inputs? | Yes. Every number rendered is a config constant, and each carries key, value, unit, provenance class, description and (where standard-derived) citation. The formula registry is served alongside, and a test asserts every formula's declared config keys resolve to real constants. |
| 3 | Is anything animated or displayed that is not backed by a real backend event or value? | No. There is no animation. Every panel renders API data; when the API is unreachable the page says so rather than showing placeholder figures. |
| 4 | Is any DEMO_CONFIG constant presented as if it were a government rule? | No. 90 of 97 constants are marked `DEMO_CONFIG` with a chip; the 7 standard-derived ones carry their Sphere / PMAY-G / NDMA citation inline. A test refuses to construct a non-DEMO constant without a citation. |
| 5 | Does the provenance panel honestly distinguish real / derived / synthetic per layer, without blending? | Yes. Per-class counts are separate, never summed into one "data sources" figure. The registry currently holds only the two datasets that genuinely exist; real datasets land in Slice 2 and are registered then, not promised now. |
| 6 | Are the same figures identical across every screen that shows them? | Yes, structurally: there is one API, one generated type package, and no client-side arithmetic. |
| 7 | Does the opening 20 seconds avoid looking like a generic dashboard? | Partially. The visual foundation (palette, tabular figures, wordmark, dense panels, no KPI card wall) is in place, but the cold open and Command Centre are Slice 12 and Slice 3. The root route redirects to the screen that is genuinely built rather than showing an empty dashboard. |
| 8 | Does every recommendation screen make it unambiguous that the SDMA decides? | Yes for what exists. The decision-authority notice and the synthetic-scenario disclaimer are served by the API and rendered on the page. There are no recommendation screens yet. |
| 9 | Does the demo run start to finish with the network off and no LLM key? | Yes for this slice. No runtime external call exists: the frontend talks only to the API, and the API reads only local files. Fonts are self-hosted by the build. |
| 10 | Can the optimiser breach capacity? | Not applicable yet. Slice 7. |
| 11 | Is there any feature that looks impressive but changes no decision? | The layer catalogue lists 17 layers of which 0 are available. That is a roadmap, not a capability, and it is labelled "Not yet built" on every card. Kept because it makes the honest build state visible; it will become the real layer control in Slice 3. |
| 12 | Would this survive "walk me through exactly how you got this number", live? | Yes for the constants and formulas on screen. Computed values start in Slice 3. |

### Red-team finding

**The weakest point in this slice is that the layer catalogue and the formula
registry describe engines that do not exist yet.** A hostile reader could call
that a promise dressed as a feature. Two things were done about it: every layer
carries an `available` flag that is `false` and renders as "Not yet built", and
the integrity gate refuses to let a layer be marked available while its datasets
are absent from the provenance registry - enforced by
`_validate_layers` and covered by a test. The catalogue therefore cannot lie in a
later slice either.

Secondary finding: the fixture gate currently passes trivially because there are
no fixtures. That is honest but weak, so the gate was written and tested against
deliberately broken fixtures (out-of-bbox coordinates, duplicate IDs, malformed
JSON, households exceeding population) rather than against an empty directory.
Six tests fail the gate on purpose.

### Verification

- 56 backend tests pass (`pytest`, `apps/api`).
- `ruff check apps/api scripts` clean.
- Fixture integrity gate passes standalone and on API startup.
- `tsc --noEmit` clean; `next build` succeeds.
- Page verified rendering live API values in a production build, not dev mode.

---

## Slice 2 - study area, real open data, derived surfaces and the calibrated synthetic layer

**Date:** 2026-09-09
**Commit:** `feat(data): establish Chamoli study area, provenance registry and validated demo dataset`

### What actually works end to end

Six real open datasets are vendored for the Alaknanda corridor. Fourteen terrain
and hydrology surfaces are computed from the real DEM by standard published
methods. Twelve habitations and six candidate sites are generated from those
surfaces, validated by the blocking integrity gate, served by the API and
rendered on a shaded-relief plate of the corridor with a panel that states, side
by side, what was measured and what was assumed.

### Gate answers

| # | Question | Answer |
|---|---|---|
| 1 | Is any part of this an LLM guessing, dressed as a computed score? | No. There is still no LLM call in the codebase. Every surface is a documented geomorphometric computation; every generated attribute is either read off a real raster or comes from a named DEMO_CONFIG assumption. |
| 2 | Can every number on screen be traced in one hop? | Yes. Each derived layer carries its method (Horn 1981, Riley 1999, Priority-Flood, D8, Renno/Nobre HAND) and its observed range, served from the build manifest. Habitation attributes trace to either a raster sample or a named constant. |
| 3 | Is anything displayed that is not backed by a real value? | No. The terrain plate is a render of the vendored DEM; the markers are drawn at the coordinates in the fixtures. Nothing is drawn that the API did not return. |
| 4 | Is any DEMO_CONFIG constant presented as a government rule? | No. The 18 generation constants are chipped DEMO_CONFIG in the transparency panel, and the study-area screen carries an explicit "what was assumed, not measured" list. |
| 5 | Does the provenance panel distinguish real / derived / synthetic without blending? | Yes. Ten datasets: six REAL_OPEN with source URLs and licences, two SYNTHETIC_CALIBRATED, two DEMO_CONFIG. Counts are reported per class, never summed. |
| 6 | Are the same figures identical across screens? | Yes. Totals are computed once in the API and rendered as received. |
| 7 | Does the opening avoid looking like a generic dashboard? | Improving. The root now opens on the corridor itself - real shaded relief with the settlements on it. The cinematic cold open is still Slice 12. |
| 8 | Is it unambiguous that the SDMA decides? | Yes for what exists. The synthetic-scenario disclaimer and the tenure limitation are served by the API and shown at the top of the study-area screen. |
| 9 | Does it run with the network off? | Yes, and this is now tested. `scripts/ingest.py` is the only code that touches the network; every connector's `load` path reads the vendored artifact and raises if it is absent, and a test asserts a connector without its artifact fails rather than fetching. |
| 10 | Can the optimiser breach capacity? | Not applicable yet. Slice 7. |
| 11 | Any feature that looks impressive but changes no decision? | The waterway-distance surface is computed but nothing reads it yet; it is retained because the flood sub-model in Slice 3 consumes it directly. If Slice 3 does not use it, it comes out. |
| 12 | Would this survive "walk me through exactly how you got this number"? | Yes for terrain and hydrology: the methods are named, cited, unit-tested against analytically known cases, and the surfaces reproduce from the DEM in about 20 seconds. |

### Red-team finding

**The sharpest attack on this slice is the demographic composition.** CLAUDE.md
asks for proportions calibrated to Census/SECC district figures. The Census
district tables are not available through any endpoint reachable without
credentials, and the accessible mirrors are unverifiable third-party copies. Two
options were available: cite a mirror and hope, or state the truth. ASTRA states
the truth - the demographic shares are ASTRA assumptions for a Himalayan hill
district, they appear as DEMO_CONFIG constants with that wording, and the
study-area screen lists them under "what was assumed, not measured" before anyone
asks. Everything that *could* be grounded in real data is: placement, elevation
band, slope, buildable land cover, road access, and settlement size integrated
from the WorldPop population surface. The weight-sensitivity analysis in Slice 10
is what turns this from a weakness into an answer.

Secondary finding: the historical inventory holds 251 incidents across the
Uttarakhand-Himalaya window but only 23 inside the study bbox. That is a thin
positive set for a ROC-AUC back-test. It is recorded here now so Slice 10 reports
the sample size alongside the figure rather than quietly presenting an AUC
computed on 23 points as if it were robust.

### Verification

- 102 backend tests pass, including terrain maths checked against analytically
  known answers (45-degree plane, pit filling, HAND on a uniform slope).
- `ruff check apps/api scripts` clean.
- Integrity gate: PASS, 6 checks, 18 fixture records, 10 datasets.
- Determinism: regenerating the fixtures from the same surfaces reproduces every
  record exactly.
- `tsc --noEmit` and ESLint clean; `next build` succeeds; study-area page verified
  against a production build serving live API data.

---

## Slice 3 - multi-hazard susceptibility engine, analytical red zones and the map surface

**Date:** 2026-09-09
**Commit:** `feat(hazard): multi-hazard susceptibility engine and analytical red-zone mapping`

### What actually works end to end

Three hazard sub-models score a 387 x 432 grid of 100 m cells over the corridor
by weighted overlay of normalised factors computed from the real DEM, land
cover, road network, incident inventory and rainfall archive. The composite
preserves dominance rather than averaging it away. The classified surface is
cleaned to a minimum mapping unit, grown by the configured buffer and published
as 335 non-overlapping zone polygons, each carrying its area, dominant hazard,
hazard mix, mean and maximum composite, intersected population, evidence
confidence and rule version. The Risk Explorer renders all of it on MapLibre and
deck.gl, and clicking anywhere returns the full factor decomposition.

### Gate answers

| # | Question | Answer |
|---|---|---|
| 1 | Is any part of this an LLM guessing, dressed as a computed score? | No. Still no LLM anywhere in the codebase. Every score is `100 x sum(w_f x n_f)` over surfaces derived from real data. |
| 2 | Can every number on screen be traced in one hop? | Yes, and it is now demonstrable: click any cell and the panel lists, per hazard, each factor's measured value, normalised value, weight and contribution, with the formula ID, formula version, engine version and config version underneath. A test asserts the published contributions sum to the published score. |
| 3 | Is anything displayed that is not backed by a real value? | No. The basemap is a render of the vendored DEM, the overlay is the computed composite, the polygons are the computed zones, the markers are fixture coordinates. There is no animation. |
| 4 | Is any DEMO_CONFIG constant presented as a government rule? | No. The zone thresholds are stated on screen with the percentile of the corridor distribution they sit at, and chipped DEMO_CONFIG in the transparency panel. |
| 5 | Does the provenance panel distinguish real / derived / synthetic without blending? | Yes. Every factor carries its own provenance class through to the decomposition panel. |
| 6 | Are the same figures identical across screens? | Yes. Zone summary, class shares and habitation composites are computed once in the engine and rendered as received. |
| 7 | Does the opening avoid looking like a generic dashboard? | Yes. The root opens on the map, dominated by real terrain with the classified surface over it. The cinematic cold open is still Slice 12. |
| 8 | Is it unambiguous that the SDMA decides? | Yes. Every zone feature carries `classification_label: ASTRA analytical classification`, the zones response carries the decision-authority line, and the panel footer states both it and the synthetic-scenario disclaimer. |
| 9 | Does it run with the network off? | Yes. MapLibre uses no token and no external tiles: basemap, hazard overlay, zone geometry and roads are all served by the ASTRA API from local files, and the style declares no sprite or glyph server. |
| 10 | Can the optimiser breach capacity? | Not applicable yet. Slice 7. |
| 11 | Any feature that looks impressive but changes no decision? | The waterway-distance surface from Slice 2 is still unused: the flood sub-model ended up using HAND and channel distance from the DEM-derived network, which is better evidence than mapped-waterway distance. It stays for the route work in Slice 6 and comes out if unused there. |
| 12 | Would this survive "walk me through exactly how you got this number"? | This is the slice where that question gets answered on screen. Composite 94.9 at Rauligaon: Flood 79.4, driven by height above drainage 7.33 m normalised to 0.897 at weight 0.42, contributing 0.377; plus Cloudburst 61.9 as the second hazard at lambda 0.25. |

### Red-team finding

**The first version of this slice reported each habitation's hazard as the
maximum composite within 300 m, and every settlement came out between 97 and
100.** That is technically defensible and practically useless: it destroyed the
ability to rank, which is the entire point of the product. The habitation score
is now the composite at the settlement's own cell, with the footprint mean and
maximum reported alongside as the spread around it. The corrected spread is 34
to 95 across the twelve habitations, with three different dominant hazards.

Second finding from the same pass: the initial thresholds put 10% of the corridor
in Critical and produced a single 190 km2 polygon - a "zone" too coarse for
anyone to act on. Thresholds now sit near the 95th, 82nd and 57th percentiles of
the corridor's own composite distribution, which is stated in the constant
descriptions and rendered on screen. A calibration choice, labelled as ASTRA's
choice wherever it appears.

Third: two factors CLAUDE.md lists - fault/lineament proximity for landslides and
historical inundation overlap for floods - have no obtainable open dataset for
this corridor. They were removed from the weight sets and the remaining weights
renormalised, with a comment in the config saying why. A neutral placeholder
constant would have looked like evidence and would not have been one.

### Verification

- 144 backend tests pass, 32 of them new: normalisation ramps against hand
  arithmetic, weighted overlay against a hand-computed expected score, the
  dominance-preserving composite against its own formula, exact behaviour at
  every classification threshold, confidence bounded and independent of the
  score, kernel density conserving total weight, single-cell speckle rejected by
  the minimum mapping unit, and zone classes proven non-overlapping.
- API tests assert that published factor contributions sum to the published
  score, that the composite equals the dominance formula, and that zone
  population totals agree with the habitation layer.
- `ruff` clean, integrity gate PASS, `tsc --noEmit` clean, ESLint clean,
  `next build` succeeds.
- The Risk Explorer was driven in a real browser against a production build: the
  map renders, layers toggle, opacity works, clicking a habitation returns its
  decomposition. Zero console errors.

---

## Slice 4 - exposure, vulnerability, history and phased relocation prioritisation

**Date:** 2026-09-10
**Commit:** `feat(priority): exposure, vulnerability and phased relocation prioritisation`

### What actually works end to end

Every habitation now carries four separately computed quantities - hazard over
its footprint, exposure, vulnerability and recency-weighted incident history -
combined into a priority score by declared weights, and assigned to Immediate,
Short-term or Medium-term by documented thresholds plus override rules that name
themselves when they fire. The Habitation Priority screen shows the ranked list
grouped by phase, colours the map by phase, and opens a reasoning drawer that
reproduces the whole calculation, component by component and factor by factor.

### Gate answers

| # | Question | Answer |
|---|---|---|
| 1 | Is any part of this an LLM guessing, dressed as a computed score? | No. Four weighted components, each decomposed into named factors with measured values. |
| 2 | Can every number on screen be traced in one hop? | Yes. The drawer shows priority = sum of four contributions, then each component's own factor table with measured, normalised, weight and contribution. Tests assert the published score equals the sum of the published contributions to within 0.02. |
| 3 | Is anything displayed that is not backed by a real value? | No. Phase colours on the map come from the computed phase; the override chip appears only when a rule actually fired. |
| 4 | Is any DEMO_CONFIG constant presented as a government rule? | No. Tier thresholds are described in the config as a policy choice about how much a district can act on at once, and that framing is on screen. |
| 5 | Does the provenance panel distinguish real / derived / synthetic without blending? | Yes, and it now matters: exposure and vulnerability factors are marked SYNTHETIC_CALIBRATED, history REAL_OPEN, hazard DERIVED. The habitation confidence is explicitly lower than the terrain confidence because of that mix. |
| 6 | Are the same figures identical across screens? | Yes. Habitation hazard composite matches between the Risk Explorer and the Priority screen because both render the same engine output. |
| 7 | Does the opening avoid looking like a generic dashboard? | Yes. Both built screens are map-dominant with a reasoning panel, not a KPI wall. |
| 8 | Is it unambiguous that the SDMA decides? | Yes. The decision-authority line, the scenario disclaimer and the history caveat sit in the drawer footer, and "priority is a ranking score, not a probability" is at the top of the list. |
| 9 | Does it run with the network off? | Yes; nothing new reaches outside. |
| 10 | Can the optimiser breach capacity? | Not applicable yet - and this slice is careful about that. `CAPACITY_BLOCKED` exists in the vocabulary but is never assigned, because no capacity engine has run. Every row instead lists the checks still pending: matched capacity (Engine 4) and route reliability (Engine 5). |
| 11 | Any feature that looks impressive but changes no decision? | No new ones. The waterway-distance surface is still on notice for Slice 6. |
| 12 | Would this survive "walk me through exactly how you got this number"? | Yes. Devgarh Tok, priority 61.1: hazard 0.875 x 0.35 = 0.306, exposure 0.146 x 0.25 = 0.036, vulnerability 0.558 x 0.25 = 0.139, history 0.859 x 0.15 = 0.129. Escalated to Immediate by the named Critical-zone vulnerability override, not by its score. |

### Red-team finding

**The first run of this engine ranked every habitation between 27 and 40, with
nothing reaching Immediate or Short-term, and the cause was a real modelling
error rather than a threshold problem.** Vulnerability was computed as a weighted
average of demographic shares, which lands near 0.2 for any realistic settlement
because those shares are individually small - so a component carrying a declared
weight of 0.25 was contributing about a fifth of that in practice, while hazard
(naturally 0.5-0.9) dominated. The components were on incomparable scales and the
configuration was quietly not doing what it said.

The fix is a stated reference profile: vulnerability is scaled against the score
of an acutely vulnerable settlement (25% elderly, 15% under five, 6% disability,
5% medically dependent, 70% low-income households, 100% weak construction), each
element a DEMO_CONFIG constant. Vulnerability now spans 0.51-0.58 across the
corridor and its weight carries what the config says it carries. A test asserts
that the reference profile scores 1.0 and that a resilient settlement scores
below 0.25.

Two related findings from the same pass. The history factor was dead - a
five-year decay constant against an inventory whose most recent record is nine
years old left every habitation at 0.00-0.05, discarding the one genuinely real
evidence layer in the priority score; the constant is now ten years, with the
reasoning in its description. And the confidence component named
`evidence_recency` was actually measuring how much incident evidence exists
nearby, so it was renamed `evidence_support` to say what it measures.

Standing weakness, recorded rather than hidden: the tier thresholds were revised
after seeing the score distribution. That is legitimate calibration - the
thresholds are a policy statement about how much a district can move at once, and
they are labelled as such - but it is exactly the kind of choice that the weight
sensitivity analysis in Slice 10 has to test rather than assert.

### Verification

- 184 backend tests pass, 40 of them new: exposure against fixed references and
  its saturation point, vulnerability against the reference profile in both
  directions, history decay and radius cut-off, the priority sum against hand
  arithmetic, exact behaviour at all three tier thresholds, both override rules
  firing only inside a Critical zone, and both being named when they fire.
- API tests assert the components arrive separately, that published contributions
  reproduce the published score, that phase totals account for every resident,
  and that pending constraint checks are declared on every row.
- `ruff` clean, gate PASS, `tsc --noEmit` clean, ESLint clean, `next build`
  succeeds, screen driven in a real browser with zero console errors.

---

## Slice 5 - suitability gates, usable area, multi-constraint carrying capacity and bottleneck analysis

**Date:** 2026-09-10
**Commit:** `feat(capacity): multi-constraint carrying-capacity engine with bottleneck analysis`

### What actually works end to end

Every candidate site is now put through five binary suitability gates, measured
for buildable ground on the real land-cover, slope and drainage surfaces, and
assessed for capacity service by service against the published per-person norms.
The effective capacity of a site is the minimum across its services, the
bottleneck is the argmin, and the marginal intervention table says what one unit
of each intervention would unlock and which service would bind next. The
Relocation Sites screen renders all of it: sites ordered availability-first, the
binding service drawn in red as the shortest bar on the screen, the projected
next constraint outlined in amber when an intervention row is hovered, every gate
with its observed value against its threshold, and the tenure limitation stated
before anyone asks for it.

The corridor answer is a real and uncomfortable one: 3 of 6 sites clear every
gate, giving 2,467 people of effective capacity against 2,519 residents assessed
- 52 short - and the marginal table shows exactly which single intervention
closes the gap.

### Gate answers

| # | Question | Answer |
|---|---|---|
| 1 | Is any part of this an LLM guessing, dressed as a computed score? | No. Every capacity figure is `supply / norm` or `supply x norm` against a cited Sphere / PMAY-G constant. The one ML component refines land cover and nothing else. |
| 2 | Can every number on screen be traced in one hop? | Yes. Each service row carries its supply, its norm, the norm's unit, provenance and citation. Effective capacity is the smallest of them; the panel names which. |
| 3 | Is anything displayed that is not backed by a real value? | No. The marginal headline sentence is assembled from `capacity_before`, `capacity_after`, `capacity_gain` and `next_bottleneck` returned by the API - it is a rendering of computed numbers, not prose about them. The hover projection uses the same fields and says explicitly that the bars still show today's assessment. |
| 4 | Is any DEMO_CONFIG constant presented as a government rule? | No. The water, sanitation, health and site-area norms are cited to Sphere; the gate thresholds, the intervention unit sizes and the measurement radius are `DEMO_CONFIG` and appear in `GET /model/config` as such. |
| 5 | Does the provenance panel distinguish real / derived / synthetic without blending? | Yes. Usable area is marked `REAL_OPEN` because it is measured on WorldCover and the Copernicus DEM; the service supplies are `SYNTHETIC_CALIBRATED` and carry that on each row. |
| 6 | Are the same figures identical across screens? | Yes. Population assessed on this screen is the same sum the Priority screen ranks, both from the same fixtures through the same API. |
| 7 | Does the opening avoid looking like a generic dashboard? | The Sites screen is a diagnostic, not a KPI wall: four figures, then the bottleneck bars. The cold open itself is Slice 12. |
| 8 | Is it unambiguous that the SDMA decides? | Yes, and this screen carries the sharpest limitation in the product: ASTRA does not verify land ownership, tenure or encumbrance. It is stated in the header, not buried in a tooltip. |
| 9 | Does it run with the network off? | Yes. The Sentinel-2 composite, the WorldCover clip and the trained refinement raster are all vendored; nothing is fetched at request time. |
| 10 | Can the optimiser breach capacity? | Not yet applicable, but this slice sets the constraint it will be held to: a site failing any gate is excluded from the district total and is marked "not available for allocation", and a test asserts the total excludes it. |
| 11 | Any feature that looks impressive but changes no decision? | The hover projection was one, and was fixed rather than kept - see below. |
| 12 | Would this survive "walk me through exactly how you got this number"? | Yes. Panduri Terrace: 5.24 ha of buildable footprint / 45 m2 per person = 1,164 land capacity; water supply / 15 L/person/day = 812; 812 is the smallest, so effective capacity is 812 and water binds. One borewell at 15,000 L/day raises it to 1,164, at which point land binds. |

### Red-team finding

**The weakest point was the intervention hover, and it was a fabricated-feature
problem hiding inside honest data.** Hovering an intervention row re-coloured the
capacity bars to highlight `next_bottleneck` in the same red used for the current
bottleneck - while the bar lengths, being the present per-service capacities, did
not move. The screen was therefore asserting "this service binds capacity" about
a service that does not, using values that describe a different scenario. Every
number was real; the composition of them was not. Fixed by giving the projected
constraint its own visual language (amber, dashed outline, distinct from red) and
adding a caption that states the projection in full and ends with "Bars show the
assessment as it stands today". This is exactly the class of failure section 2.3
forbids, and it survived one review before being caught.

Two further findings from the same pass:

**The site list was ordered by effective capacity alone, which put a site failing
a hard gate at the top with the largest number on the screen.** A gate failure is
binary - the site is not available at any capacity - so ranking it above the
sites that are available inverted the decision the screen exists to support.
Sites now sort availability-first, gate-failed rows are dimmed and state which
gate they fail, and the detail panel opens with "Not available for allocation".

**The API declared none of the dependencies it actually imports.**
`pyproject.toml` listed FastAPI, Pydantic and uvicorn while the analytical core
has imported numpy, scipy, rasterio, shapely and pyproj since Slice 2. Every
local run worked because the development environment had them; a clean CI
checkout or a Docker build would not have. They are now declared as runtime
dependencies, with the fetch-and-build tooling (`requests`, `pillow`,
`scikit-learn`) in a separate `data` extra, since none of it runs at request
time.

**Standing limitation, recorded rather than hidden:** access capacity is a route
throughput question and the route engine is Slice 6. Rather than assume access is
unconstrained, every site reports it as a pending constraint with the reason, and
a test asserts no `ACCESS` service capacity is ever scored before that engine
exists.

### Verification

- 222 backend tests pass, 33 of them new: per-service arithmetic against hand
  calculation, the norm and citation on every row, argmin selection including a
  deliberate tie, theoretical-vs-effective separation, intervention gain for a
  binding and a non-binding service, intervention ranking, the marginal sentence
  assembled from computed fields, all five gates passing and failing at their
  thresholds, the three-condition usable-area intersection, contiguous-patch
  selection including an unusable centre, no-coverage reported as no coverage
  rather than zero area, refinement agreement driving confidence, and against the
  real corridor: effective never exceeding theoretical, the binding service being
  the lowest, availability-first ordering, district totals excluding gate
  failures, access declared pending, and determinism across two runs.
- `ruff` clean, fixture gate PASS (18 records, 11 datasets), OpenAPI exported and
  TypeScript contracts regenerated, `tsc --noEmit` clean, ESLint clean,
  `next build` succeeds, and the Sites screen was driven in a real browser -
  selection, hover projection and gate-failure states - with zero console errors.

---

## Slice 6 - route reliability, survivability and closure analysis

**Date:** 2026-09-10
**Commit:** `feat(routes): route reliability and survivability analysis`

### What actually works end to end

The OpenStreetMap extract is now a routed graph: 392 tagged road ways split at
their shared OSM nodes into 529 segments, each carrying a class-derived speed, a
bridge flag and hazard exposure sampled from the composite surface along its own
length. Every one of the 72 habitation-to-site pairs is routed twice, and each
route reports travel time, distance, reliability, hazard-exposed length, longest
continuous exposure, bridges crossed and the named stretches of road that decide
whether the journey happens. The Access & Routes screen draws the network
coloured by segment failure probability, draws the selected route, fits the
camera to it, and lets an official close any segment and re-route the whole
corridor against the open network.

Slice 5's one declared gap is closed: ACCESS is now a scored capacity row on
every site, supplied by this engine, and the pending-constraint notice is gone.

The corridor's answer is again a real one. Forty-eight of 72 routes clear the
60% reliability threshold; eleven of twelve habitations can reach a site that is
both suitable and reliably reachable, and one - Bhairaunkhal - cannot, which is
intelligence rather than a missing result. Closing a single 69 m bridge on the
Joshimath-Malari road changes 36 routes, drops 13 below the threshold and leaves
six habitations with nowhere suitable they can reach.

### Gate answers

| # | Question | Answer |
|---|---|---|
| 1 | Is any part of this an LLM guessing, dressed as a computed score? | No. Reliability is a product over segments, each segment's failure probability a documented function of its exposed length and its bridge flag. |
| 2 | Can every number on screen be traced in one hop? | Yes. The route panel lists each point of failure with its own probability and the reason it qualifies, and a test asserts the route reliability equals the product of the published per-leg figures. |
| 3 | Is anything displayed that is not backed by a real value? | No. Segment colours come from the computed `p_fail`; the route line is the geometry the router returned; the closure table is two real assessments compared. |
| 4 | Is any DEMO_CONFIG constant presented as a government rule? | No. Every speed, coefficient and threshold is `DEMO_CONFIG` and served on the assessment with its description. |
| 5 | Does the provenance panel distinguish real / derived / synthetic? | Yes. The road geometry is `REAL_OPEN` OpenStreetMap; the routes computed from it are `DERIVED`, and each route reports what share of it ASTRA actually scored. |
| 6 | Are the same figures identical across screens? | Yes, and one bug here was exactly that - see below. The habitation list, the header totals and the site options are now all read from one assessment object. |
| 7 | Does the opening avoid looking like a generic dashboard? | The screen is map-dominant with a ranked list and a reasoning panel. The cold open is Slice 12. |
| 8 | Is it unambiguous that the SDMA decides? | Yes; the decision-authority line closes the panel, and the closure tool is explicitly a what-if against the open network. |
| 9 | Does it run with the network off? | Yes. The graph is built from the vendored Overpass extract; nothing is fetched at request time. |
| 10 | Can the optimiser breach capacity or use an unusable route? | Not yet applicable, but the constraint it will be held to now exists: a pair below the reliability threshold is `feasible: false`, not merely expensive, and the optimiser slice must consume that. |
| 11 | Any feature that looks impressive but changes no decision? | The fastest-versus-safest contrast came close - see below. It is kept because it is correct and because the reason it rarely fires is itself the finding. |
| 12 | Would this survive "walk me through exactly how you got this number"? | Yes. Simalkot to Sarauli Bench: 23.7 km over 12 segments, 92 min at class speeds, reliability 0.81 as the product of twelve survival probabilities, the two worst being 7,575 m of the Joshimath-Malari road at exposure 0.81 (5.2%) and 4,738 m of the Joshimath-Auli road at 0.79 (4.2%), neither with any way around it. |

### Red-team finding

**The first version of the failure model made a route's survivability a property
of OpenStreetMap's mapping habits rather than of the road.** Failure probability
was a flat per-segment value, so the same stretch of road reported different
reliability depending on how many junctions happened to be mapped along it - and
this corridor has ways running 20 km without one beside ways of 2 m. Fixed by
scaling hazard failure with *exposed length*: `1 - (1 - c) ** (mean exposure x
length / reference)`. A test now asserts directly that one segment and the same
road split into four give identical reliability. The same fix removed a second
distortion, where taking the maximum exposure over a 20 km way saturated every
long road at the worst cell it touched, so the model stopped discriminating
between them.

**The stated SAFEST objective is not a shortest-path problem, and pretending it
was produced a safest route that was less reliable than the fastest one.**
`time x (1 + alpha x risk)` is a property of the finished route; minimising it
edge by edge is not the same thing, and at alpha 20 one pair came back with a
"safest" route seven minutes slower and four points *less* reliable. The engine
now generates three candidate paths - quickest, most reliable, and the
edge-weighted compromise - and scores each against the stated objective, which
costs three Dijkstra runs and guarantees the safest route is never worse. A test
asserts that across the whole corridor.

**Unscored road was being treated as safe road.** The Overpass extract runs past
the study bounding box, and segments outside the scored hazard surface came back
with exposure 0.0 - which the router read as a perfectly safe road and therefore
preferred. Segments with no coverage at all are now dropped from the graph and
counted (`unscored_segments`), and every route reports the share of its length
that was actually scored, with the caveat on screen when it is below 100%.

**One screen was showing two different assessments at once.** After a closure was
evaluated, the habitation list drew its "route blocked" badge from the
closed-network result while the reliability figure beside it still came from the
baseline. Both are true; on the same row they contradict each other. The header
totals, the list and the site options now all read one assessment object, and the
route panel is labelled "open network" while a closure is active.

**Calibration recorded rather than hidden.** The route constants inherited from
Slice 1 were written for a per-segment model and produced a corridor in which no
route at all cleared the threshold. They were re-set for the length-scaled model:
1.5% failure per kilometre of fully exposed road, 1% per bridge structure. These
are planning-horizon figures - whether a road is usable across the weeks a phased
relocation runs - and the descriptions say so. They were chosen so the model
discriminates rather than saturates, which is legitimate calibration and exactly
the kind of choice the Slice 10 sensitivity analysis has to test rather than
accept.

**A finding worth stating plainly:** the fastest and safest routes are the same
road for almost every pair in this corridor, and that is not a shortcoming of the
router. *(Corrected in Slice 8: at the time of writing this was true for every
pair, because the safest route was chosen on the time-and-risk objective and that
objective preferred speed. With SAFEST now chosen reliability-first, twelve of
the seventy-two pairs show a real trade. The redundancy finding below is
unchanged and is still the reason the number is twelve rather than seventy-two.)* The valley network has 15 independent loops across 529 segments, and 76%
of segments have no alternative at all - removing one disconnects the network.
There is rarely a second road to choose. The screen says this in its own computed
numbers rather than leaving a feature looking broken, and a test asserts the
claim so it cannot quietly stop being true.

### Verification

- 274 backend tests pass, 41 of them new: travel time against class speeds, the
  failure formula against its documented form, bridge risk as an independent
  factor, compounding rather than doubling with length, split-invariance of route
  reliability, the reliability product, infeasibility below the threshold with a
  stated reason, the off-network walk, the safest route taking a detour and never
  being less reliable, the trade-off sentence carrying its own numbers, closures
  forcing a detour and reporting no path rather than a slow one, continuous
  versus cumulative hazard exposure, points of failure ranked and marked where no
  alternative exists, and against the real corridor: every pair evaluated, every
  site snapped to the main network rather than an isolated stub, both feasible
  and infeasible pairs present, geometry drawn in travel order to within 2% of
  the reported distance, closing the busiest bridge degrading real routes,
  determinism across two runs, and access capacity equal to route count times its
  norm.
- `ruff` clean, fixture gate PASS, OpenAPI exported and TypeScript contracts
  regenerated, `tsc --noEmit` clean, ESLint clean, `next build` succeeds, and the
  Risk, Priority, Sites and Routes screens were all driven in a real browser -
  including selecting a habitation, routing it, closing a point of failure and
  evaluating the impact - with zero console errors.

---

## Slice 7 - constrained relocation optimisation with counterfactual explanations

**Date:** 2026-09-10
**Commit:** `feat(optimizer): constrained relocation optimisation with counterfactual explanations`

### What actually works end to end

Every earlier engine now feeds one decision. OR-Tools CP-SAT assigns people from
habitations to sites, in phases, subject to site effective capacity, a phase
capacity ramp, per-phase travel ceilings, route reliability above the threshold,
suitability gates and a household-integrity floor - all of them hard, none of
them penalties. It minimises a weighted sum of unmet demand scaled by priority,
travel burden, route risk, site overload, livelihood disruption, community
fragmentation and phase delay, and every term is served on the response with the
constant that produced it.

Livelihood disruption is computed, not a kilometre rule: routed commute back to
the habitation's own livelihood centre, the weakest road class on that link, that
link's reliability, and routed travel time from the site to the nearest trunk
road. The four components and their arithmetic are on the assignment panel.

"Why not that site" re-solves with the assignment forced and reports what
happened - either the named hard constraint that removes it, or the objective
delta and who loses their place. Forcing Panduri Sera's 258 residents onto
Sarauli Bench returns "feasible but worse by 8,299 on the objective", with the
before and after objective values, from an actual second solve.

The corridor's plan places 1,144 of 2,519 residents across two sites, and the
reason for the rest is on screen with the constraint named for each habitation.
The headline finding is the one an SDMA can act on: **1,188 assessed places at
Sarauli Bench are unused and only twelve of the 1,375 people still waiting can
reach them.** The binding constraint on this district's relocation plan is the
road, not the site.

### Gate answers

| # | Question | Answer |
|---|---|---|
| 1 | Is any part of this an LLM guessing, dressed as a computed score? | No. The plan is a CP-SAT solution; the explanations are re-solves and constraint records. The one narrative sentence on the screen is assembled from the plan's own totals. |
| 2 | Can every number on screen be traced in one hop? | Yes. Each assignment carries its route, its reliability, its objective contribution and the four-row livelihood table whose contributions sum to the figure above them. The objective is broken out term by term. |
| 3 | Is anything displayed that is not backed by a real value? | No. Assignment lines on the map are the route geometry the solver planned against, not straight lines between centroids. The solver status, wall-clock time and objective are the solver's own. |
| 4 | Is any DEMO_CONFIG constant presented as a government rule? | No. All seven objective weights, the capacity ramp shares, the travel ceilings and the household floor are `DEMO_CONFIG` and served on the plan response. |
| 5 | Does the provenance panel distinguish real / derived / synthetic? | Yes. Livelihood factors are `DERIVED`; the routes they are measured on are derived from `REAL_OPEN` OSM geometry; the populations moved are `SYNTHETIC_CALIBRATED`. |
| 6 | Are the same figures identical across screens? | Yes. Site effective capacity on the plan matches the Sites screen; route reliability matches the Routes screen; both come from the same engines through the same API. |
| 7 | Does the opening avoid looking like a generic dashboard? | The plan screen is map-dominant with a movement list and a reasoning panel. The cold open is Slice 12. |
| 8 | Is it unambiguous that the SDMA decides? | Yes; the decision-authority line closes the panel, and approval and override are Slice 11. |
| 9 | Does it run with the network off? | Yes. CP-SAT is a local library and every input is vendored. |
| 10 | **Can the optimiser output an assignment that breaches capacity, an unusable route or an unsuitable site?** | **No, and it is proved rather than asserted.** `validate()` runs after every solve including the fallback and raises on a capacity breach, a phase-ceiling breach, an assignment that was never an allowed option, one below the household floor, or people who do not add up. Three tests forge each of those plans and assert the exception; a fourth re-checks the real corridor plan against the capacity engine and the route engine directly. |
| 11 | Any feature that looks impressive but changes no decision? | The greedy fallback changes no decision by design - it is demo insurance. It is labelled `FALLBACK` in the response, in the status chip and in a note, and a test asserts it is never better than the solver. |
| 12 | Would this survive "walk me through exactly how you got this number"? | Yes. Panduri Sera to Panduri Terrace, 258 residents: 12.2 km routed over 33 min at 89% reliability; livelihood disruption 0.710 = 0.40x(100.9/90 capped at 1.0) + 0.20x0.70 + 0.20x(1-0.49) + 0.20x(20.5/60). It is in the short-term phase because that is where the phasing engine put Panduri Sera, and it goes to S-05 rather than S-06 because forcing S-06 costs 8,299 more on the objective. |

### Red-team finding

**The map was being destroyed and rebuilt on every render, and it took a
disappearing camera to notice.** `RiskMap` created its MapLibre instance in an
effect whose dependency list included the `onSelectPoint` callback - and every
caller passes an inline arrow, so the dependency changed on every render, the
cleanup ran `map.remove()`, and a fresh map was built. It was invisible until the
counterfactual panel appeared and the camera silently jumped back to the whole
corridor. The callback is now read through a ref and the creation effect depends
only on the things that genuinely define the map. This was costing a full WebGL
teardown per keystroke of state on three screens.

Three modelling findings, all caught by looking at what the plan actually said:

**The site overload penalty was acting as a hard cap.** At the inherited value of
400 per person, overloading a site cost more than the priority-weighted penalty
for leaving someone unmoved, so the solver preferred to strand residents in a red
zone rather than use the last 15% of a site. That is not a plan an SDMA could
defend. Reduced to 60 with the reasoning written into the constant, and a test
asserts that a site's last places are used rather than people being left behind.

**Habitations were locked to a single phase, which made 'Immediate' a label
rather than a plan.** Engine 3 assigns a habitation its phase; the first version
of this engine treated that as its only phase, so a village of 487 people had to
move entirely within the immediate ramp or not at all, and a site 63 minutes away
was rejected outright rather than becoming a short-term destination. A phase is
now the *earliest* phase, and a village may move in stages - which is what a
phased relocation is. Fragmentation still charges splits between places, not
between phases, because a village moved to one site over two phases is not a
divided community.

**Then the opposite problem: nothing made the solver prefer moving people
sooner.** With phases open, the cost of moving a habitation now and in the medium
term were identical, and the solver picked whichever the search reached first.
Adding a delay penalty fixed the ordering - and at the first value tried, 120 per
person per phase, it also cut coverage by a hundred people, because a late move
stopped being worth making at all. That is the wrong trade: delay should
discipline *when* people move, never *whether*. Set to 25, with a test that
asserts the number of people placed is identical with the delay penalty at zero.

**Standing limitation, recorded:** the market-access component of livelihood
disruption uses routed travel time to the nearest trunk road as a proxy for where
a district's markets, banks and offices are. That is a real measurement of a real
thing, but it is a proxy, and the constant says so.

### Verification

- 324 backend tests pass, 50 of them new: capacity, phase-ramp and travel-ceiling
  constraints; priority winning contested capacity; cheaper routes and less
  disruptive destinations preferred; the household floor; staged moves to one
  site counting as one destination; splits happening when they place more people;
  reproducibility across runs; the delay penalty ordering without shrinking the
  plan; three forged plans rejected by post-solve validation; the fallback
  labelling itself and never beating the solver; livelihood disruption as a
  weighted sum, at zero for a perfect destination, worse on a worse road class at
  the same travel time, at maximum for an unreachable livelihood centre, and
  demonstrably not a distance rule; and on the real corridor: every resident
  accounted for, every rejected pairing naming its constraint, unmet demand
  explained rather than counted, stranded capacity measured against who can reach
  it, blocked habitations flagged, and counterfactuals for both a blocked and an
  allowed site.
- `ruff` clean, fixture gate PASS, OpenAPI exported and TypeScript contracts
  regenerated, `tsc --noEmit` clean, ESLint clean, `next build` succeeds, and the
  Plan screen was driven in a real browser - selecting a movement, watching the
  camera hold, and running a counterfactual re-solve - with zero console errors.

---

## Slice 8 - what-if recalculation engine with structured impact diff

**Date:** 2026-09-10
**Commit:** `feat(scenarios): what-if recalculation engine with structured impact diff`

### What actually works end to end

A scenario is a first-class, versioned, stored object carrying a list of typed
perturbations, not a state the interface happens to be in. `POST /simulate`
applies those perturbations to a **copy** of the engine context and then runs the
entire chain a second time - hazard surface, red-zone polygonisation, exposure
and vulnerability, phase tiering, road network, route reliability, site capacity,
CP-SAT optimisation - and returns a structured diff against the baseline the rest
of the product is already showing. The baseline is read from the warmed caches
and is never recomputed and never replaced, so before and after are two complete
assessments of the same corridor rather than one assessment rendered twice.

Each perturbation enters the chain at exactly one stage, and the stage decides how
far it propagates:

| Perturbation | Enters at | Cascades to |
|---|---|---|
| `RAINFALL_MULTIPLIER`, `LANDSLIDE_SHIFT` | Engine 1, the factor surfaces | zones, priority, phases, the road graph's own hazard exposure, suitability gates, the plan |
| `POPULATION_MULTIPLIER` | Engine 2, the habitation records | exposure, vulnerability counts, priority, demand, the plan |
| `SITE_CAPACITY_LOSS`, `SERVICE_UPGRADE`, `SITE_DISABLED` | Engine 4, the candidate set and its supplies | effective capacity, the binding bottleneck, the plan |
| `ROAD_CLOSURE` | Engine 5, the routed graph | route reliability and travel time, feasible pairs, access capacity, the plan |

Rainfall scales both the intensity surface and the count of extreme-rain days,
because a wetter monsoon is not only heavier on its worst day - it has more of
them. The landslide shift moves the *terrain instability inputs*, never the
finished score, so the factor decomposition on the risk drawer stays an honest
account of how the number was produced. A hazard scenario rebuilds the road graph
rather than reusing the baseline one, because segment failure probability is
sampled from the composite surface: reusing it would leave every road as
survivable as it was before the storm, which is exactly the assumption a what-if
exists to test. A withdrawn site is removed from the candidate set rather than
zeroed, because a site with zero capacity would still appear on the capacity
screen with a bottleneck and an intervention that would "unlock" it.

The diff is field by field and nothing in it is recomputed: zone area and
population by class, per-habitation priority, rank, phase and hazard, per-site
suitability, effective capacity, binding bottleneck and the hard gates each side
fails, per-pair route reliability and travel time, per-movement people before and
after, newly-immediate population, and totals for placed, unmet, capacity and
feasible routes. The headline sentence is assembled from those computed deltas.

The What-If screen puts the controls on the left, the map in the centre with an
explicit **Baseline / Scenario** toggle, and the diff on the right. Every control
maps to exactly one typed perturbation and says which engine it enters at. The
map redraws on the scenario's own zone geometry and the scenario plan's own route
geometry, and can be flipped back to the baseline's. Three presets fill the
controls and simulate nothing until the scenario is run.

`GET /routes/critical-segments` is new and makes the closure control honest: it
ranks the corridor by the residents of the *solved plan* whose assigned journey
crosses each segment, so the road a user closes is the one that tests the plan
rather than an arbitrary edge of the network.

### Gate answers

| # | Question | Answer |
|---|---|---|
| 1 | Is any part of this an LLM guessing, dressed as a computed score? | No. The whole chain re-runs. The headline sentence is assembled from the computed deltas by a function with no model in it. |
| 2 | Can every number on screen be traced in one hop? | Yes. Each row of the diff carries both sides, and both sides are values the engines returned. The per-stage millisecond breakdown says where the time went. |
| 3 | Is anything displayed that is not backed by a real value? | No. The map's scenario view is `zones_after` and `plan_after` from the response. There is no scenario animation and no synthetic delta anywhere in the component. |
| 4 | Is any DEMO_CONFIG constant presented as a government rule? | No. The sliders are scenario inputs, not model constants; the model constants they feed are unchanged and still served on `/model/config`. The response carries the engine and model-config versions. |
| 5 | Does the provenance panel distinguish real / derived / synthetic? | Yes, unchanged. A scenario perturbs inputs; it does not reclassify them. |
| 6 | Are the same figures identical across screens? | Yes. `plan_after` and `zones_after` come back through the *same* serialisers the Plan and Risk screens use, so a simulated plan cannot drift into a parallel shape. The baseline half of every comparison is read from the same caches those screens read. |
| 7 | Does the opening avoid looking like a generic dashboard? | The What-If screen is map-dominant with a comparison toggle. The cold open is Slice 12. |
| 8 | Is it unambiguous that the SDMA decides? | Yes; the scenario disclaimer and the decision-authority line close the diff panel. |
| 9 | Does it run with the network off? | Yes. Every input is vendored and the whole chain is local. |
| 10 | Can the optimiser breach capacity under a scenario? | No. Post-solve validation runs on the scenario solve too, and a test re-checks a scenario plan against the scenario's own capacity and route engines. |
| 11 | Any feature that looks impressive but changes no decision? | The presets were the risk. They are labelled as filling the controls and nothing else, and the "Monsoon escalation" preset was retuned (see below). |
| 12 | Would this survive "walk me through exactly how you got this number"? | Yes. Rainfall x1.5: critical zone 109.6 -> 328.8 km2, three habitations move to Immediate, 561 residents newly requiring immediate action, and the plan places 561 instead of 1,144 - because S-02 and S-06 keep every unit of their service capacity but stop clearing the `OUTSIDE_HAZARD_ZONES` gate, which the site panel says in those words. |

### Red-team findings

**1. A site could lose its gates and the diff would read as calm.** Suitability
is a hard yes-or-no, so when the expanded red zones swallowed S-02 and S-06 their
*effective capacity numbers did not move at all* - the sites simply stopped being
candidates. The first version of the site table showed "455 -> 455 (+0)" beside a
plan that had just lost half its destinations, which is the most misleading thing
on the screen precisely because every number in it was true. `SiteDelta` now
carries the failed gates on each side, and the panel says: *still has capacity,
but no longer a candidate: now fails OUTSIDE_HAZARD_ZONES. A gate is a yes or no,
so the capacity figure beside it does not fall.*

**2. The monsoon preset produced a degenerate comparison.** At rainfall x1.8 with
a +0.10 landslide shift, every candidate site fails a gate, total effective
capacity goes to zero and the plan places nobody. That is a real model output and
an interesting finding, but as the headline preset it produces a "before and
after" whose after is empty. The preset is now rainfall x1.5 - three suitable
sites down to one, 1,144 residents placed down to 561, three phase changes - and
the extreme case remains reachable by dragging the slider. The comment in the code
says exactly why.

**3. The scenario store was unbounded.** Every run wrote a scenario into a module
dict that `GET /scenarios` lists. A user dragging a slider for a minute would have
turned the scenario list into a junk drawer and the process into a slow leak.
Scenarios are still stored - that is what lets a result be traced back to the
changes that produced it - but through `remember_scenario`, which keeps the last
50 and never evicts the baseline. Scenario ids also carry a random suffix now:
two simulations can land in the same millisecond, and a colliding id would have
silently overwritten the scenario an earlier result was traced to.

**4. Perturbations outside the range they mean anything in were accepted.** A
capacity loss of 5.0 clamped to "everything", and a landslide shift of 4 was
applied to surfaces that only understand -1..1. Both are now refused with a reason
that names the quantity, and the interface shows the engine's own words rather
than a friendlier sentence nothing checked. Land and access upgrades are refused
too: land capacity is measured off the buildable ground and access capacity off
the road network, and neither is a supply anyone can deliver to a site.

**5. `127.0.0.1` was not an allowed CORS origin.** The same machine, a different
origin to a browser - so a developer or a deployment reaching the frontend on the
loopback address got a page whose every API call failed silently and a blank map.
Found by the browser test, fixed in the default origin list rather than in the
test.

### Verification

- **367 backend tests pass, 43 of them new.** Beyond the scenario engine's own
  unit tests, the causality of each perturbation is asserted at its boundary: a
  service upgrade moves one site's capacity and leaves the zone areas, the route
  set and every priority score untouched; a road closure moves routes and leaves
  every habitation's hazard score untouched; a population change moves exposure
  and the plan and leaves the hazard surface untouched. Plus: the baseline plan is
  identical after a simulation; deltas reconcile with the before-and-after totals;
  a null scenario produces an empty diff; the same scenario run twice gives the
  same answer; a scenario that withdraws every site is refused; every resident is
  still accounted for under a scenario and the scenario plan respects every hard
  constraint.
- **Six Playwright tests drive the real screen in a real browser against the real
  API.** They assert on *change*, not presence - each reads a metric before and
  after and requires it to move in the direction the perturbed engine says it
  should - and they assert zero console errors and no horizontal overflow at
  tablet width. A screen that rendered a baseline twice would pass a smoke test
  and fails these.
- `ruff` clean, fixture gate PASS (6 checks, 18 records, 0 errors), OpenAPI
  exported and TypeScript contracts regenerated, `tsc --noEmit` clean, ESLint
  clean, `next build` succeeds.

---

## Slice 9 - live event ingest, incremental re-scoring and the execution pipeline

**Date:** 2026-09-10
**Commit:** `feat(realtime): live event ingest, incremental re-scoring and execution pipeline`

### What actually works end to end

`POST /events` accepts observations - rainfall, incident reports, field evidence,
road status - registers them in a process-wide log, and starts a real pipeline
run on a worker thread. `GET /runs/{id}/stream` publishes that run's stage events
as Server-Sent Events under the exact names CLAUDE.md section 9 specifies:
`stage_started`, `stage_progress`, `stage_completed`, `warning`, `stage_failed`,
closing with `run_completed`. The React Flow graph on the Live Operations screen
renders those frames and nothing else.

**The re-scoring is genuinely spatially scoped, and it is proved rather than
claimed.** Each event carries a footprint - the ground the observation speaks for
- with a linear taper that reaches exactly zero at the stated radius, so the
circle drawn on the map is the ground the arithmetic used. Events are written
into a copy of the standing input surfaces inside those footprints and nowhere
else; the union of footprints gives a row/column window plus a one-cell margin;
only that window is scored; the result is spliced into the standing surfaces.

That splice is only legitimate because the factor stack is a **pure per-cell
function** of its inputs once the density normalisation ceilings are pinned to
the baseline. `normalise_by_percentile` now accepts a pinned ceiling, and
`HazardEngine(ceilings=...)` uses it. Pinning is also correct on its own terms: a
live system whose yardstick moved with every observation would produce a score
this minute that could not be compared with the one on screen from last minute.
`test_a_windowed_rescore_equals_a_full_recomputation` asserts, for all three
surface event kinds, that the windowed result is identical - composite, zone
class, confidence and every per-hazard score - to a full recomputation over the
same inputs. If that test ever fails, the word "incremental" in the interface is
a lie and the window has to go.

A run re-scores 7%-23% of a 167,184-cell grid depending on the footprint, and
says so on the HAZARD node: cells re-scored, cells in the grid, **cells that
actually moved**, the largest movement in points, and cells that changed zone
class. Zone polygons are re-derived in full, because cleaning and buffering are
morphological operations that read across the whole surface - and the note on
screen says exactly that rather than implying otherwise.

Each kind enters at one surface and cascades from there: rainfall raises
intensity and the extreme-rain-day count; an incident adds severity-weighted
density through the *same kernel at the same bandwidth* the historical inventory
was smoothed with; field evidence raises the terrain instability input, never the
finished score; a road-status report touches no hazard surface at all and enters
at the route engine. Downstream, the run re-ranks habitations, re-routes the
corridor, re-checks suitability gates and effective capacity, re-solves the plan,
and then checks the *standing* plan movement by movement against the new state.

**Plan invalidation is specific.** The Decision Brief stage compares every
movement in the baseline plan against what this run computed: is the destination
still a candidate, is the route still above the reliability threshold, has the
origin entered the immediate tier. It produces a banner naming the count of
movements and residents affected and a list giving the reason per movement.
Nothing is silently re-planned; the decision-authority line sits on the banner.

The demonstration feed is a vendored `DEMO_CONFIG` fixture served as **data to be
posted**. The client posts each step to the same `POST /events` an external feed
would use, and each step triggers a real run. Its road-closure step ships a
placeholder that resolves at replay time from `/routes/critical-segments`, so the
feed closes whatever the *current* solved plan leans on hardest rather than an id
that mattered when the fixture was written. Replaying it end to end takes the
critical zone from 109.6 to 149.3 km2, moves ten habitations' hazard scores,
moves Rauligaon from Medium-term to Short-term, drops feasible routes 49 to 36,
and cuts the plan from 1,144 residents placed to 258 with six movements flagged
for review.

`POST /live/reset` discards the log and returns to the baseline. Live state is
*derived* from the log rather than edited in place, so a reset is a real reset
with no residue for the next demonstration.

### Gate answers

| # | Question | Answer |
|---|---|---|
| 1 | Is any part of this an LLM guessing, dressed as a computed score? | No. There is no model anywhere in this slice. Every stage payload is a field of the object that stage produced. |
| 2 | Can every number on screen be traced in one hop? | Yes. Each graph node lists the payload keys of its own `stage_completed` frame, and an E2E test asserts the cells-re-scored figure on the HAZARD node equals what `/live` reports for that run. |
| 3 | **Is anything animated that is not backed by a real backend event?** | **No, and this is the slice where that question is sharpest.** A node is idle until a `stage_started` frame arrives for it, running until its `stage_completed` does, and warning only when the backend emitted a `warning`. The graph header shows a live count of SSE frames received; an E2E test reads that counter and requires at least fifteen. With no run, the graph renders an explanation instead of an idle animation. |
| 4 | Is any DEMO_CONFIG constant presented as a government rule? | No. The feed carries a visible `DEMO CONFIG` chip and says in its own description that it is an ASTRA demonstration script, not a record of a real storm. |
| 5 | Does the provenance panel distinguish real / derived / synthetic? | Yes. Every ingested event carries a provenance class and shows its source; the feed's observations are `SYNTHETIC_CALIBRATED` from a `DEMO_CONFIG` script and are labelled as such. |
| 6 | Are the same figures identical across screens? | Yes. `/live/zones` and `/live/plan` come back through the *same* serialisers the Risk and Plan screens use, so a live plan cannot drift into a parallel shape. |
| 7 | Does the opening avoid looking like a generic dashboard? | Live Operations is map-dominant with the pipeline beneath it. The cold open is Slice 12. |
| 8 | Is it unambiguous that the SDMA decides? | Yes, and more so here than anywhere: the banner says *Plan requires review*, names what is affected, and carries the decision-authority line. ASTRA never re-plans on its own authority. |
| 9 | Does it run with the network off? | Yes. Ingest, re-score and the stream are all local; SSE is a plain HTTP response. |
| 10 | Can the optimiser breach capacity under a live run? | No. Post-solve validation runs on the live solve too. |
| 11 | Any feature that looks impressive but changes no decision? | The field-evidence step was the risk - see below. |
| 12 | Would this survive "walk me through exactly how you got this number"? | Yes. 38,478 cells re-scored of 167,184: the union of five observation footprints, bounded to a row/column window plus one cell. 29,328 of them moved, by up to 31.1 points, and 10,283 changed zone class. Rauligaon moved tier because its footprint-sampled hazard rose enough to carry its priority past the Short-term threshold of 45. |

### Red-team findings

**1. An incident report was landing three orders of magnitude off scale.** The
obvious implementation - drop the severity weight into the cell and divide by the
cell area - put a single report at ~300 weighted incidents/km2 against a
whole-grid historical maximum of 0.36. It did not *look* wrong on screen only
because the incident-density factor was already clipped at its normalisation
ceiling across that footprint, so the saturation was invisible. A new incident is
now smoothed through the same kernel at the same bandwidth the historical
inventory uses, so it lands in the same units on the same scale, and a test
asserts one report cannot dwarf the entire historical record.

**2. The Gaussian had no edge, and the window did.** Writing that kernel
unclipped put a vanishing but non-zero change on cells outside the circle the map
draws and outside the window the re-score covers - which is simultaneously a lie
on the map and a splice that silently drops those cells. Caught by the
windowed-equals-full test, which failed on exactly the incident case. The kernel
is now clipped to the drawn footprint: beyond three sigmas there is under 1% of
its mass, and the contract that nothing outside the footprint moves is worth more
than it. The incident's stated radius is also overridden to three bandwidths, so
what is drawn is what was used.

**3. Two feed steps looked like they did nothing.** Field evidence and an
incident report both left the critical-zone area unchanged to one decimal place,
which reads as a step that exists to pad the demonstration. They were not inert -
the field report raised Dungri Tok's hazard from 41.0 to 43.1 and re-classified
34 cells - but nothing on screen said so. The run now measures and reports *what
moved*, not only what was recomputed: cells changed, largest movement,
re-classified cells, and the per-habitation hazard and tier movements. A small
true effect reported precisely is worth more than a headline that leaves a viewer
guessing.

**4. "Play feed" would have stopped after one observation.** The replay loop read
a ref mirrored from state during render, and state is not committed by the time
the loop's second iteration runs - so the ref still read `false` and the loop
broke. The ref is now set directly. An E2E test plays the whole feed unattended,
clicking nothing after the first press, and requires every observation to arrive.

**5. Reset visibly did not reset.** A run's completion starts a refresh at the
same moment the user can press Reset; the in-flight refresh landed afterwards and
put the discarded numbers back on screen. Found by an E2E test that failed with
"12,078" where it expected "0". Refreshes now carry a generation token and drop
their own result if a reset has happened since.

**6. The map kept its first canvas size.** MapLibre sizes its canvas once, from
the container as it was at creation, and every screen here puts the map in a CSS
grid that settles after mount - on this screen, more than once as panels grow. A
`ResizeObserver` now re-sizes it. This was a latent defect on every map screen,
not just this one.

**7. Two threading faults that appear once, in front of an audience.** The event
log was iterated on the run thread while the request thread appended to it, and
event ids were minted without a lock so two concurrent posts could collide. Both
are now under a lock, with a `snapshot()` for every read.

### Verification

- **416 backend tests pass, 49 of them new.** The load-bearing one is
  `test_a_windowed_rescore_equals_a_full_recomputation`, parameterised over all
  three surface event kinds. Alongside it: footprints scale with the square of
  the radius and clip at the study-area edge; an event changes its footprint and
  provably nothing outside it; rainfall below the extreme threshold adds no
  extreme day; an incident lands on the historical scale; field evidence touches
  the instability input and no other surface; a road report touches no hazard
  surface at all; the log reports the latest status per segment so a reopened
  road does not stay shut; every stage emits with a real elapsed time and a
  payload it computed; a closed road reaches the optimiser and costs the plan;
  an observation far from anything says the plan still holds; the run history is
  bounded; the stream replays from the first stage; and the full feed replay
  escalates the corridor and ends in review.
- **Seven Playwright tests drive the real screen against the real API**, including
  one that plays the entire feed unattended. They read the SSE frame counter, they
  cross-check a number on a graph node against `/live`, they require every stage
  node to reach a terminal state, and they assert zero console errors and no
  horizontal overflow at tablet width.
- The fixture integrity gate now validates the demonstration feed too: every step
  must parse, land inside the study bbox, carry a positive footprint, and say
  what it is. `PASS: 7 checks, 18 fixture records, 11 datasets, 0 errors.`
- `ruff` clean, OpenAPI exported (40 paths, 125 schemas) and TypeScript contracts
  regenerated, `tsc --noEmit` clean, ESLint clean, `next build` succeeds.

---

## Slice 10 - hazard model back-testing and weight sensitivity analysis

**Date:** 2026-09-10
**Commit:** `feat(validation): hazard model back-testing and weight sensitivity analysis`

### What actually works end to end

`scripts/backtest.py` computes the whole credibility layer from the vendored data
and the committed configuration with a fixed seed, writes
`data/derived/validation.json`, and `GET /validation` serves that artifact. The
Model & Provenance screen renders it. Nothing is entered by hand, nothing is
recomputed per request, and `--check` re-runs the computation in CI and fails the
build if a headline figure has moved - so a refactor that quietly changes an AUC
on screen breaks the build rather than turning into a better-looking number.

**The numbers this produced, which are the numbers on the screen:**

| Variant | Independent | ROC-AUC | 95% CI | Top 10% | Top 20% |
|---|---|---|---|---|---|
| As deployed | **no** | 0.88 | 0.82–0.93 | 61% | 72% |
| Cross-validated (6-fold) | yes | **0.79** | 0.68–0.88 | 44% | 56% |
| Terrain and rainfall only | yes | 0.66 | 0.52–0.79 | 39% | 44% |

Per sub-model, as deployed: landslide 0.88, flood 0.79, cloudburst 0.68. Across
1,000 Monte Carlo runs perturbing all 18 weights by ±20%, the habitation ranking
holds a median Spearman correlation of **0.993** with the baseline, worst run
0.972, and the **top-5 set is unchanged in 85%** of runs. Two habitations - H-07
Thalgaon Sera and H-12 Sarauli Tok - are flagged weight-sensitive and say so on
both the validation table and the priority list.

**The discipline that makes those numbers worth anything.** The recorded incident
inventory is *an input to the model*: kernel density over those points is one of
the landslide sub-model's six weighted factors. Scoring the model against the
same points is circular and inflates the AUC, which is why the "as deployed"
figure reads 0.88 and is labelled **not independent** on screen. The headline is
the 6-fold cross-validation, where the incident-density factor is rebuilt from
the other folds only before re-scoring, and the background points are scored on
that same fold surface so both sides of every comparison come from one model. The
strictest variant holds the incident factor at zero weight entirely and asks
whether the physical model alone puts past failures on dangerous ground: 0.66,
lower than both, and shown anyway.

Every AUC carries a percentile bootstrap interval and its sample count, because
the sample inside this corridor is 18 incidents and an AUC from 18 points without
an interval is a number pretending to be a measurement. The success-rate curve is
assembled from each incident's *area rank on the surface it was actually scored
on*, so the cross-validated curve is genuinely cross-validated rather than
silently recomputed against the full-data surface.

The sensitivity analysis re-derives the composite from the cached normalised
factor surfaces - the same arithmetic `score_hazard` does, proved equal to the
engine's own output by test - which is what makes 1,000 runs take 31 seconds
instead of hours. Perturbed weight sets are renormalised so each still sums to
one; a perturbation of zero leaves the ranking bit-for-bit identical, and a
larger perturbation moves it more. Both are tests.

The confidence surface is rendered as a **hatch**, not a fade, and drawn *over*
the hazard layer rather than blended into it. Fading low-confidence ground would
read as *less hazardous*, which is exactly the conflation §5.2 exists to prevent.
A test asserts on the real surfaces that the most susceptible ground is not the
best-evidenced ground - confidence is genuinely not multiplied into the score.

### Gate answers

| # | Question | Answer |
|---|---|---|
| 1 | Is any part of this an LLM guessing? | No. There is no model in this slice; the statistics are computed from ranks in numpy. |
| 2 | Can every number on screen be traced in one hop? | Yes. The seed, the fold count, the exclusion radius, the background count and the sample size are all on the panel beside the figures they produced. |
| 3 | Is anything displayed that is not backed by a real value? | No. The success curve is drawn from the points the back-test computed; the dashed diagonal is labelled as the random-classification reference. |
| 4 | Is any DEMO_CONFIG constant presented as a government rule? | No. The perturbation size, run count, background count and top-k are `DEMO_CONFIG` and appear in the constants table. |
| 5 | Does the provenance panel distinguish real / derived / synthetic? | Yes, unchanged. The incident inventory is `REAL_OPEN` and the panel says so; its bias is stated in the limitation. |
| 6 | Are the same figures identical across screens? | Yes. The rank-stability chip on the Priority screen is read from the same `/validation` payload the Model screen renders. |
| 7 | Does the opening avoid looking like a generic dashboard? | Cold open is Slice 12. |
| 8 | Is it unambiguous that the SDMA decides? | Yes; the response carries the decision-authority line and the screen already closes with it. |
| 9 | Does it run with the network off? | Yes. The artifact is on disk; the statistics need no network and no external library beyond numpy. |
| 10 | Can the optimiser breach capacity? | Unchanged from Slice 7; still proved by post-solve validation. |
| 11 | **Any feature that looks impressive but changes no decision?** | The confidence surface was the candidate, and the answer is honest rather than convenient: in this corridor it barely varies (0.62–0.79, no cell in the Low band), because three of its four inputs are near-uniform here. The screen says that in those words instead of implying a richer signal. |
| 12 | Would this survive "walk me through exactly how you got this number"? | Yes. 0.79 is the Mann-Whitney U of 18 held-out incident scores against 12,000 background scores, both taken from the six fold surfaces; 0.68–0.88 is the 2.5th–97.5th percentile of 2,000 bootstrap resamples; 56% is the share of those 18 whose area rank on their own fold surface is within the top fifth. |

### Red-team findings

**1. The first cross-validated success curve was not cross-validated.** The AUC
used held-out scores, but the curve was recomputed from the full-data surface at
the original incident cells - so the cross-validated row's curve was byte-identical
to the uncross-validated row's, and the top-20% capture read 72% for both. It
looked right and was wrong in the flattering direction, which is the worst
combination. The curve is now built from each incident's **area rank on the
surface it was actually scored on**, and the cross-validated figure fell from 72%
to 56%, where it belongs.

**2. Background points were being scored by a different model from the
positives.** In the first version the CV positives came from fold surfaces while
the background came from the full-data surface. The resulting AUC would have
measured the difference between two models as much as the model's skill. Both
sides now come from the same fold surface, pooled across folds.

**3. Removing the incident factor tripped the engine's own integrity guard, and
that was correct.** `score_hazard` refuses to score a factor with no declared
weight - exactly the silent discrepancy that guard exists to catch. Rather than
weaken the guard for the convenience of the test, the terrain-only variant holds
the factor at **zero weight** and redistributes its share proportionally, which is
the honest way to say "this contributes nothing" and keeps the other factors'
relative emphasis intact. A test asserts that ratio is preserved.

**4. A narrow confidence surface was nearly dressed up as a rich one.** No cell in
this corridor falls in the Low band; the whole surface lies between 0.62 and 0.79.
Rendering it against the full 0–1 scale would have produced a uniform sheet that
said nothing; rendering it against its own range without saying so would have
implied more contrast than exists. It is rendered against the observed range *and*
the observed range is printed beside it, with an explanation of why it is narrow
here: three of the four inputs barely vary in a corridor covered by one DEM at one
resolution.

**5. The confidence overlay was almost a fade.** A fade would have made
low-confidence ground look *safer*. It is a hatch, drawn over the hazard layer,
and it survives greyscale printing - which colour alone does not.

### Verification

- **456 backend tests pass, 40 of them new.** The statistics are checked against
  cases whose answers are known: AUC is exactly 1.0 for perfect separation, 0.0
  for perfect inversion, 0.5 for an all-ties surface and near 0.5 for a coin
  flip; Spearman is ±1.0 for identical and reversed orderings; a perfect model's
  success curve captures everything in a sliver of area and a worthless one
  traces the diagonal; a smaller sample yields a wider bootstrap interval. Then
  the discipline: background points are provably away from every incident,
  sampling and the whole back-test are reproducible, the uncross-validated figure
  is asserted to read *higher* than the honest one, the terrain-only weight set
  is asserted to contain no incident evidence while preserving the other
  factors' ratios, the fast composite re-derivation is asserted equal to the
  engine's own output, a zero perturbation leaves the ranking untouched, a larger
  one moves it more, and confidence is asserted not to be folded into the score.
- **Six Playwright tests read the panel as a judge would**, requiring the awkward
  figures to be present: the "not independent" label on the flattering variant,
  the terrain-only row despite it being the lowest, the sample size and interval
  in the headline, the limitation paragraph unhidden, at least one habitation
  actually flagged weight-sensitive, the stability chip on the Priority screen,
  and the confidence layer toggling on the Risk Explorer. Zero console errors.
- CI now runs `scripts/backtest.py --check`, which recomputes and fails on drift.
- `ruff` clean, fixture gate PASS (7 checks), OpenAPI (42 paths, 134 schemas) and
  TypeScript contracts regenerated, `tsc --noEmit` clean, ESLint clean, `next
  build` succeeds, 19 Playwright tests pass against the real API.

---

## Slices 11-13 - intelligence layer completion, judge-ready presentation, documentation

**Date:** 2026-09-10
**Commits:** `feat(demo): judge-ready presentation flow, decision brief and demo mode`;
`docs: architecture, provenance, decision model, acceptance matrix and self-audit`

### What was inspected first

Slice 11's evidence intake, decision ledger, override-with-consequence, narration
adapter and ask allowlist were already committed and working end to end. What
was genuinely missing against CLAUDE.md: the Command Centre (the root still
redirected to the Risk Explorer), the cold open, Demo Mode, the comparative
panel, the Decision Brief (`POST /brief`), the judge-journey E2E, and every
Slice 13 document except SCALING and this log.

### What actually works end to end

- **Command Centre at `/`.** A six-second cold open with a cited real event, then
  the corridor resolves layer by layer, each step gated on the map actually
  having loaded. It settles on the current situation, the action required per
  phase, ASTRA's recommendation, the priority queue, what unlocks capacity, and
  one primary action: **Run monsoon escalation**, which posts the real feed to
  `POST /events` while the SSE-driven execution graph follows each run.
- **Decision Brief.** `GET /brief/preview` builds it from the standing state
  (live run if any, else baseline) and writes nothing; `POST /brief` writes a
  ledger row and freezes the payload in a new `briefs` table; `/brief/{id}` is a
  printable paper document carrying brief ID, audit decision ID, input hash,
  phased actions, bottlenecks, route risks, confidence, the comparison,
  assumptions, limitations and the decision's current ledger state.
- **Static hazard map vs ASTRA decision mode**, built server-side from computed
  values, on the Command Centre and in the brief.
- **Demo Mode**: nine steps across every screen, each caption fetched from the
  API at that moment, the What-If step run through the What-If screen's own
  controls; Escape stops it.
- **Navigation** grouped as Decide / Analyse / Stress-test / Trust, and the
  "How this works" statement plus the decision-authority line on every screen,
  served by `/health`.

### Gate answers

| # | Question | Answer |
|---|---|---|
| 1 | Is any part of this an LLM guessing? | No. The brief and Command Centre are serialised engine results; the advisory paragraph is the deterministic template unless a key is configured, and then numerically validated. |
| 2 | Can every number be traced in one hop? | Yes. Brief figures come through the same serialisers as the Plan, Sites and Routes screens, and `test_brief.py` asserts equality with those endpoints. |
| 3 | Anything animated without a backend event? | The layer-resolve sequence is paced for legibility, but each step only switches on a layer the map genuinely draws, and it does not start until the basemap reports loaded. The execution graph remains SSE-only. The Demo Mode progress bar reflects the step index. |
| 4 | DEMO_CONFIG presented as a rule? | No. Tier rules in the brief say `(DEMO_CONFIG)`; assumptions list each constant's provenance and citation. |
| 5 | Provenance honest per layer? | Yes. The resolve sequence names each layer's source class; the brief carries the synthetic-scenario notice. |
| 6 | Same figures across screens? | Yes, and now tested across endpoints for the brief. |
| 7 | Opening avoids a generic dashboard? | Yes: cold open, then a map-dominant screen with one primary action and no KPI wall. |
| 8 | SDMA decides, unambiguously? | Yes: decision-authority line on every screen via the layout, on the Command Centre, and as the first callout of the brief, with a signature block. |
| 9 | Runs with the network off and no key? | Yes. The cold-open image is served by the API; narration runs in template mode. |
| 10 | Can the optimiser breach capacity? | Unchanged and still proved by post-solve validation; the brief test additionally asserts no recommended movement uses an unsuitable site or a route below threshold. |
| 11 | Impressive but changes no decision? | Demo Mode was the candidate. It is kept because each caption is a real API result and the What-If step runs a real scenario; it is escapable and never the only path. |
| 12 | Survives "walk me through this number"? | Yes for the brief: every figure names its endpoint, and the printed page carries the input hash that reproduces the state. |

### Red-team findings

1. **The test suite was writing into the demonstration ledger.** Tests filed
   evidence and recorded decisions in `data/astra.sqlite`, so Evidence & Audit
   showed test rows as if an officer had filed them, and a ledger assertion
   compared row counts under a 100-row cap, so it failed on any populated store.
   `tests/conftest.py` now points the store at a throwaway file, and the
   assertion compares the newest row instead of a count.
2. **The formula registry contradicted the engine.** The zone-class expression
   read `C >= 70 / 55 / 40` while the engine classifies at the configured 78 /
   62 / 52, so the Model screen showed two different thresholds. The objective
   expression also omitted the phase-delay term the solver minimises. Both
   expressions now match the engine and name the config keys.
3. **Every server-side fetch paid ~200 ms for `localhost`.** The API binds IPv4;
   on Windows `localhost` tries `::1` first. Measured 208 ms against 3 ms for
   `127.0.0.1`. The client default and `.env.example` now use `127.0.0.1`.
4. **Zone geometry dominated map-page payloads.** 22,539 vertices at full float
   precision. Rounding the wire format to five decimals (about 1 m on 100 m cells)
   cut `/risk/zones` from 961 KB to 598 KB and each map page by 350-400 KB; areas
   and intersections are still computed on full-precision geometry.
5. **The escalation could double-count observations.** Pressing it again on an
   already-live state would re-post the same feed. The control disables once
   observations are ingested and offers Reset; a token stops an in-flight feed
   on reset or navigation.

### Verification

- Backend: `test_brief.py` (8 new), `test_intelligence.py`, `test_api.py`,
  `test_live_api.py`, `test_route_engine.py`, and the registry and golden-config
  tests - all pass. `ruff` clean.
- OpenAPI (59 paths) and TypeScript contracts regenerated; `tsc --noEmit` and
  ESLint clean; `next build` succeeds.
- Playwright `e2e/judge-journey.spec.ts`, against the real API and production
  build: the full journey (load → live escalation → top habitation → site
  bottleneck and marginal intervention → plan why-not re-solve → bridge-closure
  what-if with assignments changed → Decision Brief with audit ID), the cold open
  and layer resolution, and Demo Mode - 3 of 3 pass, zero console errors.
