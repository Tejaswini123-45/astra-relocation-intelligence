"""Train the land-cover refinement model and compare it with ESA WorldCover.

    python scripts/build_landcover_ml.py

Runs offline against the vendored Sentinel-2 composite. Writes the refined
buildable mask and records the labelling rules, what each rule found, the
cross-validated accuracy and the agreement with the primary WorldCover path in
the derived manifest - so every figure the interface shows about this model comes
from a measured run rather than a claim.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from astra.data.rasters import read_manifest, read_raster, write_manifest, write_raster
from astra.engines.landcover_ml import (
    BUILDABLE_LABELS,
    LabelSurfaces,
    agreement,
    build_training_set,
    read_composite,
    rule_table,
    spectral_features,
    train_refinement_model,
)
from astra.settings import get_settings


def _resample_to(source: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    if source.shape == shape:
        return source
    rows = np.clip(
        (np.arange(shape[0]) * source.shape[0] / shape[0]).astype(int),
        0,
        source.shape[0] - 1,
    )
    cols = np.clip(
        (np.arange(shape[1]) * source.shape[1] / shape[1]).astype(int),
        0,
        source.shape[1] - 1,
    )
    return source[np.ix_(rows, cols)]


def main() -> int:
    settings = get_settings()
    composite_path = settings.raw_dir / "sentinel" / "sentinel2_composite_alaknanda.tif"
    if not composite_path.exists():
        print(
            f"missing {composite_path.relative_to(REPO_ROOT)}; run "
            "`python scripts/ingest.py --only sentinel2-composite`"
        )
        return 2

    import rasterio

    with rasterio.open(composite_path) as source:
        transform, crs = source.transform, source.crs
        shape = (source.height, source.width)

    features = spectral_features(read_composite(composite_path))
    print(
        f"composite {shape[0]} x {shape[1]} cells, "
        f"{features.valid.mean() * 100:.1f}% valid pixels"
    )

    derived, raw = settings.derived_dir, settings.raw_dir
    surfaces = LabelSurfaces(
        elevation_m=_resample_to(
            read_raster(raw / "dem" / "copernicus_dem_30m_alaknanda.tif").data, shape
        ),
        slope_deg=_resample_to(
            read_raster(derived / "slope_deg.tif", scale=0.5).data, shape
        ),
        upstream_area_km2=_resample_to(
            read_raster(derived / "upstream_area_km2.tif", scale=0.1).data, shape
        ),
        road_distance_m=_resample_to(
            read_raster(derived / "road_distance_m.tif").data, shape
        ),
        population_per_km2=_resample_to(
            read_raster(
                raw / "population" / "worldpop_1km_2020_alaknanda.tif"
            ).data,
            shape,
        ),
    )

    training = build_training_set(features, surfaces)
    print(f"training set: {training.sample_count} rule-labelled pixels")
    for label in training.label_names:
        signature = training.signatures[label]
        print(
            f"  {label:11s} {training.per_class[label]:4d} sampled of "
            f"{signature['available_cells']:7.0f} available  "
            f"ndvi {signature['median_ndvi']:6.3f}  ndbi {signature['median_ndbi']:6.3f}"
        )
    for skipped in training.skipped:
        print(f"  skipped {skipped}")

    model = train_refinement_model(training)
    print(
        f"random forest: {model.cross_validated_accuracy * 100:.1f}% class accuracy, "
        f"{model.buildable_accuracy * 100:.1f}% on the buildable decision "
        "(5-fold, stratified)"
    )
    for name, importance in sorted(
        model.feature_importance.items(), key=lambda item: item[1], reverse=True
    ):
        print(f"  {name:11s} importance {importance:.3f}")

    refined = model.predict_buildable(features)
    primary = _resample_to(
        read_raster(derived / "landcover_buildable.tif").data, shape
    )
    score = agreement(primary, refined.astype(float), features.valid)
    print(f"agreement with the ESA WorldCover path: {score * 100:.1f}% of valid cells")
    print(
        f"  WorldCover buildable {primary[features.valid].mean() * 100:.1f}%, "
        f"refinement buildable {refined[features.valid].mean() * 100:.1f}%"
    )

    size = write_raster(
        derived / "landcover_ml_buildable.tif",
        refined.astype("float64"),
        transform,
        crs,
        dtype="uint8",
    )

    manifest_path = derived / "manifest.json"
    manifest = read_manifest(manifest_path) if manifest_path.exists() else {"layers": {}}
    manifest["layers"]["landcover_ml_buildable"] = {
        "file": "landcover_ml_buildable.tif",
        "dtype": "uint8",
        "stored_scale": 1.0,
        "unit": "boolean",
        "bytes": size,
        "min": 0.0,
        "max": 1.0,
        "mean": float(refined.mean()),
        "note": (
            "Random Forest over Sentinel-2 spectral indices: a second opinion on "
            "buildable ground. ESA WorldCover remains the primary path."
        ),
    }
    manifest["landcover_refinement"] = {
        "model": model.model_name,
        "model_version": model.model_version,
        "algorithm": (
            "RandomForestClassifier, 250 trees, max depth 10, balanced class weights, "
            "CPU only, trained in about a second"
        ),
        "features": model.feature_names,
        "feature_importance": {
            key: round(value, 4) for key, value in model.feature_importance.items()
        },
        "classes": model.label_names,
        "buildable_classes": sorted(BUILDABLE_LABELS),
        "training_samples": model.training_samples,
        "samples_per_class": training.per_class,
        "class_signatures": training.signatures,
        "labelling_rules": rule_table(),
        "class_accuracy": round(model.cross_validated_accuracy, 4),
        "buildable_accuracy": round(model.buildable_accuracy, 4),
        "agreement_with_worldcover": round(score, 4),
        "caveats": [
            (
                "Labels come from rules over the DEM, the D8 hydrology, the road "
                "network and the WorldPop surface - never from the WorldCover product "
                "this model is compared against, which would only prove it can copy."
            ),
            (
                "The rules are approximations. Some labelled cells will be wrong, and "
                "the accuracy figures are therefore about the rules as much as about "
                "the model."
            ),
            (
                "Grass, cropland and built-up are not reliably separable at 30 m. That "
                "does not affect the decision this model feeds: all three are "
                "buildable, and buildable-or-not is the only question it is asked."
            ),
            "The model refines usable area. It scores no hazard and ranks nothing.",
        ],
    }
    write_manifest(manifest_path, manifest)
    print(f"wrote landcover_ml_buildable.tif ({size / 1e6:.2f} MB) and updated the manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
