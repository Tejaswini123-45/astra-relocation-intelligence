"""Generate the synthetic habitation and candidate-site fixtures.

    python scripts/seed_fixtures.py

Reads only vendored and derived data, writes ``data/fixtures``, and records in
``data/fixtures/generation_manifest.json`` exactly which attributes were measured
from real surfaces and which were assumed. Deterministic: the seed lives in the
model configuration, so this produces the same dataset on every machine.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from astra.data.generate import (
    SurfaceStack,
    generate_habitations,
    generate_sites,
    generation_manifest,
)
from astra.data.rasters import read_raster
from astra.data.study_area import get_study_area
from astra.domain.enums import ConfidenceBand, ProvenanceClass
from astra.domain.models import DatasetRecord
from astra.settings import get_settings


def load_surfaces() -> SurfaceStack:
    settings = get_settings()
    derived = settings.derived_dir
    raw = settings.raw_dir
    missing = [
        path
        for path in (
            raw / "dem" / "copernicus_dem_30m_alaknanda.tif",
            raw / "population" / "worldpop_1km_2020_alaknanda.tif",
            derived / "slope_deg.tif",
            derived / "hand_m.tif",
            derived / "road_distance_m.tif",
            derived / "drainage_distance_m.tif",
            derived / "landcover_buildable.tif",
        )
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "missing inputs: "
            + ", ".join(str(p.relative_to(REPO_ROOT)) for p in missing)
            + "\nrun `python scripts/ingest.py` then `python scripts/build_derived.py`"
        )

    elevation = read_raster(raw / "dem" / "copernicus_dem_30m_alaknanda.tif", name="elevation")
    slope = read_raster(derived / "slope_deg.tif", name="slope", scale=0.5)
    hand = read_raster(derived / "hand_m.tif", name="hand", scale=0.1)
    road_distance = read_raster(derived / "road_distance_m.tif", name="road_distance")
    drainage_distance = read_raster(
        derived / "drainage_distance_m.tif", name="drainage_distance"
    )
    buildable = _resample_to(
        read_raster(derived / "landcover_buildable.tif", name="buildable"), elevation
    )
    population = _resample_to(
        read_raster(raw / "population" / "worldpop_1km_2020_alaknanda.tif", name="population"),
        elevation,
    )
    return SurfaceStack(
        elevation=elevation,
        slope=slope,
        hand=hand,
        road_distance=road_distance,
        drainage_distance=drainage_distance,
        buildable=buildable,
        population_density=population,
    )


def _resample_to(source, target):
    """Nearest-neighbour resample onto the DEM grid so every surface aligns."""
    import numpy as np
    from astra.data.rasters import RasterLayer

    rows, cols = target.data.shape
    row_indices, col_indices = np.meshgrid(
        np.arange(rows), np.arange(cols), indexing="ij"
    )
    lon, lat = target.transform * (col_indices + 0.5, row_indices + 0.5)
    src_col, src_row = ~source.transform * (lon, lat)
    src_row = np.clip(np.round(src_row).astype(int), 0, source.data.shape[0] - 1)
    src_col = np.clip(np.round(src_col).astype(int), 0, source.data.shape[1] - 1)
    return RasterLayer(
        name=source.name,
        data=source.data[src_row, src_col],
        transform=target.transform,
        crs=target.crs,
    )


def main() -> int:
    settings = get_settings()
    area = get_study_area()
    stack = load_surfaces()

    habitations = generate_habitations(stack, area)
    sites = generate_sites(stack, area)
    if not habitations or not sites:
        print("generation produced no records; check the derived surfaces")
        return 1

    fixtures = settings.fixtures_dir
    fixtures.mkdir(parents=True, exist_ok=True)
    (fixtures / "habitations.json").write_text(
        json.dumps([json.loads(h.model_dump_json()) for h in habitations], indent=2) + "\n",
        encoding="utf-8",
    )
    (fixtures / "sites.json").write_text(
        json.dumps([json.loads(s.model_dump_json()) for s in sites], indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = generation_manifest(habitations, sites, area)
    (fixtures / "generation_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    _register_datasets(settings.provenance_path, manifest)

    print(f"habitations: {len(habitations)}")
    for habitation in habitations:
        print(
            f"  {habitation.id}  {habitation.name:16s} "
            f"pop {habitation.population:5d}  hh {habitation.households:4d}  "
            f"elev {habitation.elevation_m:6.0f} m"
        )
    print(f"sites: {len(sites)}")
    for site in sites:
        print(
            f"  {site.id}  {site.name:22s} "
            f"area {site.gross_area_m2 / 10000:6.1f} ha  "
            f"slope {site.mean_slope_deg:5.1f} deg  elev {site.elevation_m:6.0f} m  "
            f"road {site.distance_to_road_m:6.0f} m"
        )
    print(
        f"total population at risk: {manifest['totals']['population']:,} "
        f"across {manifest['totals']['habitations']} habitations"
    )
    return 0


def _register_datasets(provenance_path: Path, manifest: dict) -> None:
    """Record the generated layers in the provenance registry, honestly labelled."""
    payload = json.loads(provenance_path.read_text(encoding="utf-8"))
    acquired = datetime.now(UTC).date()
    generated = [
        DatasetRecord(
            id="astra-habitations",
            name="ASTRA synthetic habitations (Alaknanda corridor)",
            source="ASTRA generator, from real terrain, land cover, road and population data",
            source_url=None,
            provenance=ProvenanceClass.SYNTHETIC_CALIBRATED,
            acquired=acquired,
            processing=(
                f"{manifest['totals']['habitations']} settlements placed on the real "
                "derived surfaces (slope, elevation band, height above nearest drainage, "
                "buildable land cover, distance to the OSM road network) and sized from "
                "the WorldPop population surface. Names are fictional. Demographic "
                "composition is an ASTRA assumption, not a census value."
            ),
            resolution="point records with a 30 m placement grid",
            temporal_coverage="demonstration scenario, 2026",
            confidence=ConfidenceBand.MEDIUM,
            licence="Repository licence",
            notes=(
                "No record here corresponds to a real settlement. ASTRA never renders "
                "a hazard classification over a real named village."
            ),
        ),
        DatasetRecord(
            id="astra-sites",
            name="ASTRA synthetic candidate relocation sites (Alaknanda corridor)",
            source="ASTRA generator, from real terrain and land cover data",
            source_url=None,
            provenance=ProvenanceClass.SYNTHETIC_CALIBRATED,
            acquired=acquired,
            processing=(
                f"{manifest['totals']['sites']} candidate sites placed on contiguous "
                "buildable ground under 15 degrees slope, above the nearest channel and "
                "within reach of a mapped road. Extent measured on the real surfaces; "
                "service infrastructure supply is an ASTRA assumption."
            ),
            resolution="point records with measured contiguous extent",
            temporal_coverage="demonstration scenario, 2026",
            confidence=ConfidenceBand.MEDIUM,
            licence="Repository licence",
            notes=(
                "Land ownership, tenure and encumbrance are not verified and cannot be "
                "verified from these inputs."
            ),
        ),
    ]
    generated_ids = {record.id for record in generated}
    kept = [r for r in payload.get("datasets", []) if r["id"] not in generated_ids]
    payload["datasets"] = sorted(
        kept + [json.loads(record.model_dump_json()) for record in generated],
        key=lambda record: record["id"],
    )
    provenance_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
