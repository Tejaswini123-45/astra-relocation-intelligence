# Limitations

Stated before they are asked.

## Data

- **Habitations and candidate sites are synthetic.** They are placed from real
  terrain, land cover, roads and population surfaces, but the settlements, their
  names and their populations are fictional. Nothing in ASTRA is an official
  hazard designation of a real settlement.
- **Land tenure is not verified.** Site suitability and capacity narrow the search
  using terrain, land cover and service data. Ownership, tenure and encumbrance
  need an SDMA field survey.
- **Rainfall is coarse.** ERA5 reanalysis on a ~25 km grid, sampled at 16 points,
  cannot resolve a single cloudburst cell. The cloudburst sub-model is a
  susceptibility proxy, not a nowcast.
- **The incident inventory is biased and small.** 18 recorded events inside the
  corridor, compiled from media and official reports, biased towards roads and
  settlements, with location accuracy of up to a kilometre.
- **Coastal erosion** is implemented and unit-tested but not exercised, because the
  study area is Himalayan.

## Model

- **Weights and thresholds are expert-chosen `DEMO_CONFIG` constants**, not
  calibrated to a statutory standard. The sensitivity analysis shows the ranking is
  stable under +/-20% perturbation; it does not show the weights are correct.
- **The confidence surface barely varies in this corridor** (0.62-0.84), because
  most of its inputs are uniform here. It is reported as such.
- **Livelihood disruption** is a composite of routed travel time, connectivity,
  route reliability and destination access. It is a proxy, not a household survey.
- **The optimiser runs under a fixed time limit.** If it is reached, a greedy
  allocation is returned and labelled `FALLBACK`; it is workable, not optimal.
- **The ML component is deliberately small**: a CPU Random Forest over Sentinel-2
  indices trained on hand-labelled patches, used only to refine the ESA WorldCover
  usable-area estimate and reported with its agreement.

## Real-time

- The ingest endpoint, incremental re-scoring and SSE stream are real, but the
  demonstration feed is a **controlled replay** of a scripted rainfall escalation
  posted to that endpoint. There is no live connector to IMD or a state sensor
  network in this prototype.
- Live state is held in the API process. Restarting the API returns to the
  baseline; the audit ledger, evidence and briefs persist in SQLite.

## Human-in-the-loop and security

- **There is no authentication or role model.** The deciding officer on an
  override is a typed name, not a verified identity. A deployment would put the
  ledger behind SDMA single sign-on and role-based permissions.
- Evidence classification is by documented keyword and pattern rules. An optional
  language model may refine severity but may not change the hazard class.
- Narration is template-based unless a provider key is configured; any
  model-written number not present in the computed result discards the narration.

## Scale and deployment

- One study area, twelve habitations, six sites. `docs/SCALING.md` sets out the
  PostGIS and tiling path to district and state scale.
- Deployment is configured for Railway (`railway.json`, `railway.api.json`) and
  Docker Compose. A public URL depends on a deployment being run from an account
  with access; the prototype is verified locally, offline, with no LLM key.
