# Scaling and the persistence decision

## What this prototype runs on, and why

ASTRA stores static geospatial layers as precomputed GeoJSON/GeoParquet on disk
and keeps mutable state - scenarios, runs, evidence, decisions, audit records and
human overrides - in SQLite.

**PostGIS was considered and deliberately rejected for the prototype.** At the
demonstration scale - one study bounding box, twelve habitations, six candidate
sites, a road graph of a few thousand edges - a spatial database buys nothing
that Shapely and GeoPandas in-process do not already give us, while adding a
network dependency, a migration step and a class of deployment failure on demo
day. The prototype must also run start to finish on a laptop with the network
off; a file-backed store does that with no extra work.

This is a scale judgement, not an argument that PostGIS is wrong. It is the
right answer at a different size, and the path there is written out below so the
question has a concrete answer rather than a shrug.

## Current shape

| Concern | Prototype | Where it lives |
|---|---|---|
| Terrain, hydrology, land cover | Raster derivatives for one bbox | `data/derived/` |
| Hazard surfaces and red zones | Precomputed GeoJSON per scenario | `data/derived/` |
| Habitations, sites, road segments | Validated JSON fixtures | `data/fixtures/` |
| Provenance registry | Single reviewed JSON file | `data/provenance.json` |
| Scenarios, runs, evidence, audit, overrides | SQLite | `data/astra.sqlite` |
| Model configuration | Versioned Python module, served over the API | `apps/api/astra/domain/model_config.py` |

## Where this stops working

1. **More than one study area at a time.** Raster derivatives are computed per
   bounding box. A state-wide deployment needs tiled derivatives and a spatial
   index rather than per-area files.
2. **Concurrent writers.** SQLite serialises writes. One SDMA workstation is
   fine; a district-wide deployment with several analysts overriding plans
   concurrently is not.
3. **Ad-hoc spatial queries.** "Every habitation within 2 km of a Critical zone
   in this division" is a scan in the current design. That is the query PostGIS
   exists for.
4. **Rasters that no longer fit in memory.** The composite grid for one corridor
   is small. A state at 30 m resolution is not.

## Migration path to PostGIS

The migration is bounded because the engines never touch storage directly: they
consume domain models, and the data layer under `apps/api/astra/data` is the only
thing that reads or writes.

1. **Schema.** One table per domain entity - `habitations`, `sites`,
   `road_segments`, `hazard_zones`, `runs`, `decisions`, `audit` - with
   `geometry(Geometry, 4326)` columns and GiST indexes on every geometry.
   Provenance fields move from the JSON registry to a `datasets` table with the
   same mandatory columns the Pydantic model already enforces.
2. **Repository seam.** Replace the loaders in `astra/data/fixtures.py` and
   `astra/data/provenance.py` with repository implementations backed by
   SQLAlchemy. The return types stay identical - the same Pydantic models - so no
   engine, router or frontend type changes.
3. **Raster strategy.** Move derived surfaces to Cloud-Optimised GeoTIFF in
   object storage, read windowed with rasterio, or to `postgis_raster` if the
   deployment prefers a single store. Engine 1 already samples through a narrow
   accessor, so this is one implementation swap.
4. **Spatial predicates.** Push zone/habitation intersection and buffer
   operations from Shapely to SQL (`ST_Intersects`, `ST_Buffer`,
   `ST_DWithin`). The formula registry entries do not change: the arithmetic is
   the same, the evaluation moves.
5. **Migrations and seeding.** Alembic for schema, and the existing
   `scripts/validate_fixtures.py` gate re-pointed at the database so the same
   integrity rules run before any deployment serves a plan.

## Complexity notes

| Stage | Cost | Note |
|---|---|---|
| Hazard surface | O(cells x factors) | Cells scale with area / resolution squared. Tiling is the answer, not a faster loop. |
| Red-zone polygonisation | O(cells) plus polygon cleaning | Cleaning dominates at fine resolution; the minimum mapping unit bounds it. |
| Priority scoring | O(habitations) | Trivial at any realistic size. |
| Capacity | O(sites x services) | Trivial. |
| Routing | O(E log V) per pair, Dijkstra | Precompute a habitation-to-site travel matrix once per scenario. |
| Optimisation | CP-SAT over habitations x sites x phases integer variables | The binding practical limit. At district scale, decompose per division and solve in parallel, or relax to an LP with rounding. The greedy fallback is already deterministic and O(n log n). |
| Sensitivity analysis | O(runs x habitations) | Embarrassingly parallel. |

The honest boundary: the analytical core scales to a district on a single
machine. Beyond that, the optimiser is what needs decomposition first - not the
database.
