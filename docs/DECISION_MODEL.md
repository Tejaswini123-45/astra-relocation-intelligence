# Decision model

Every formula ASTRA uses, with the constants it runs on. The authoritative source
is the running system: the formula registry and all 183 constants are served at
`GET /model/config` and rendered on the Model & Provenance screen. Values below
are model config `1.10.0`, engine `0.6.0`.

176 constants are `DEMO_CONFIG` - ASTRA's choice, not a government rule. Seven
are standard-derived and cited (Sphere Handbook, PMAY-G, NDMA). Nothing here is a
statutory designation or an order.

## 1. Multi-hazard susceptibility (Engine 1) - `hazard.hsi`, `hazard.composite`

Per hazard `h`, on a ~100 m grid over the study area, by weighted linear overlay
of normalised factor rasters - the methodology of BIS IS 14496 (Part 2) landslide
hazard zonation and GSI practice:

```
HSI_h(x) = 100 · Σ_f w_{h,f} · n_f(x),        Σ_f w_{h,f} = 1
```

Sub-models: **landslide** (slope, ruggedness, incident kernel density, rainfall
intensity, land cover, drainage density), **flood** (height above nearest
drainage, distance to drainage, rainfall, infiltration proxy), **cloudburst /
flash flood** (extreme-rain frequency, catchment steepness, confluence density,
contributing area), and **coastal erosion** (implemented, not exercised in this
corridor). Weight sets are the `hazard.*` constants.

The composite preserves dominance rather than averaging it away:

```
C = min(100, max_h HSI_h + λ · second_highest_h HSI_h),     λ = 0.25
```

Every cell keeps its per-hazard vector, dominant hazard and ranked factor
contributions; `GET /risk/cell` returns them.

## 2. Red zones - `hazard.zone_class`

```
CRITICAL  C ≥ 78      ELEVATED  C ≥ 62      WATCH  C ≥ 52      LOW  otherwise
```

Derivation is spatial: threshold, remove slivers below the minimum mapping unit,
buffer, polygonise, intersect with habitations. Each zone carries area, dominant
hazard, hazard mix, mean and max composite, population intersected, mean
confidence and rule version. Labelled *ASTRA analytical classification*.

## 3. Exposure, vulnerability, history (Engine 2)

Hazard, exposure and vulnerability are computed and shown separately.

```
E    = 0.55 · norm(population) + 0.20 · norm(households) + 0.25 · norm(critical facilities)
V    = 0.20 elderly + 0.18 children under 5 + 0.18 disability + 0.14 medical dependency
       + 0.15 low-income households + 0.15 kutcha structures             (shares, V ∈ [0,1])
Hist = Σ_i severity_i · exp(-Δt_i / τ)   within r,   τ = 3,650 days,  r = 3,000 m
```

History is one weighted factor, never proof of future hazard.

## 4. Priority and confidence - `priority.score`, `confidence.index`

```
P = 100 · normalise( 0.35·H + 0.25·E + 0.25·V + 0.15·Hist )
```

`H` is the composite sampled over the habitation footprint. All four weights are
`DEMO_CONFIG` and every term is shown with its contribution.

```
conf = 0.30·completeness + 0.30·provenance_mix + 0.25·evidence_recency + 0.15·spatial_resolution
```

Confidence is **never multiplied into priority**. The UI says: priority is a
ranking score, not a probability; evidence confidence is reported separately.

## 5. Phase tiers (Engine 3)

| Tier | Rule |
|---|---|
| Immediate | P ≥ 55, **or** override: inside a Critical zone with V ≥ 0.55, **or** inside a Critical zone with population ≥ 400 |
| Short-term | P ≥ 45 |
| Medium-term | P ≥ 35 |
| Capacity blocked | High risk with no feasible matched capacity - flagged, not silently ranked |

Overrides are named on screen when they fire. In the optimiser each phase has its
own travel ceiling (60 / 120 / 180 min) and share of site capacity
(0.45 / 0.75 / 1.00).

## 6. Suitability and carrying capacity (Engine 4) - `capacity.*`

**Hard gates** (binary, reported by name): outside Critical/Elevated zones plus
buffer; slope below the build-safe limit; buildable land cover; above the flood
level (HAND); within road distance.

**Usable area**: ESA WorldCover buildable classes intersected with the site
footprint and slope mask; a CPU Random Forest over Sentinel-2 indices refines it
and its agreement is reported.

**Per-service capacity** `cap_s = supply_s / norm_s`:

| Service | Norm | Source |
|---|---|---|
| Land | 45 m² site area per person | Sphere Handbook (2018) |
| Covered shelter | 3.5 m² per person | Sphere Handbook (2018) |
| Water | 15 L per person per day | Sphere Handbook (2018) |
| Sanitation | 20 persons per latrine | Sphere Handbook (2018) |
| Healthcare | 10,000 persons per facility | Sphere Handbook (2018) |
| Shelter occupancy | 5 persons per unit | NDMA minimum standards of relief |
| Permanent plot | 25 m² per dwelling | PMAY-G framework |
| Power | 1 kVA per household | DEMO_CONFIG |

```
theoretical = cap_land
effective   = min_s cap_s
bottleneck  = argmin_s cap_s
gain_s      = min_s'( cap_s' | supply_s + Δ_s ) − effective        (marginal intervention)
```

Interventions are ranked by effective-capacity gain and the next binding
constraint is named, e.g. *+0.25 facility units of healthcare raises effective
capacity 1200 to 1380; the next binding constraint becomes sanitation.*

## 7. Routes (Engine 5) - `route.reliability`, `route.safest_objective`

```
R = Π_seg (1 − p_fail,seg)
```

`p_fail` per segment rises with sampled hazard exposure (coefficient 0.015 per
1,000 m reference length) and with bridge dependency (0.01). A route with
`R < 0.6` makes the site **infeasible** for that habitation, not merely
penalised. The safest route minimises `time · (1 + 2.0 · risk)`; fastest and
safest are both returned when they differ, with the trade-off stated.

## 8. Allocation (Engine 6) - `optimiser.objective`

OR-Tools CP-SAT over integer `x[h][s][phase]`, subject to phase capacity shares,
reliability ≥ 0.6, phase travel ceilings, suitability gates, household integrity
and a soft site capacity of 85%:

```
minimise  1000 · unmet_demand · priority
        +    1 · people · travel_time
        +  250 · people · route_risk
        +   60 · site_overload
        +  300 · livelihood_disruption
        +  150 · fragmentation
        +   25 · phase_delay
```

```
livelihood_disruption = w_tt·norm(routed travel to livelihood centre) + w_c·connectivity_penalty
                      + w_rr·(1 − route reliability) + w_ma·(1 − market access)
                      (commute ceiling 90 min, market ceiling 60 min; no fixed kilometre rule)
```

Fixed seed (20260191) and a 10 s time limit. If the limit is reached, a
deterministic greedy allocation is returned and labelled `FALLBACK`. A post-solve
validation asserts no capacity breach, no infeasible route and no orphan
assignment; failure is fatal, never rendered.

**Counterfactuals are re-solves.** *Why not site S for habitation H?* forces that
assignment through the same model and reports infeasibility with the binding
constraint, or the objective delta and who is displaced.

## 9. Scenarios and live ingest (Engines 7-8)

| Perturbation | Enters at |
|---|---|
| Rainfall multiplier, landslide shift | Engine 1, and everything downstream |
| Site capacity loss, service upgrade, site disabled | Engine 4 |
| Road closure | Engine 5 |
| Population multiplier | Engine 2 |

`POST /simulate` returns a structured diff: zone area, tier changes, priority,
capacity, route and assignment deltas, and population newly requiring immediate
action. `POST /events` re-scores only the cells inside each observation's
footprint and cascades through every engine, emitting SSE stage events.

## 10. Validation - `validation.success_rate_curve`, `validation.rank_stability`

ROC-AUC by Mann-Whitney U of incident scores against background scores, with
6-fold spatial cross-validation and 2,000-resample bootstrap intervals; the
success-rate curve from each incident's area rank on the surface it was scored
on; rank stability over 1,000 runs of ±20% weight perturbation. See
`docs/VALIDATION.md`.

## 11. Narration and the Decision Brief

Narration receives finished structured results only. Every number of 10 or more
in model-written prose must match a source value within 2% (percentages of
fractions admitted), or the prose is discarded for the deterministic template.

The Decision Brief is read, not recomputed: plan totals through the Plan
serialiser, capacity totals through the Sites serialiser, route dependencies
through the What-If serialiser. Its evidence-confidence line is the modal band
across habitations, with ties resolving to the lower band.
