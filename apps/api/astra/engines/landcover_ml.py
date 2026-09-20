"""The one machine-learning component in ASTRA: land-cover refinement.

CLAUDE.md section 2.1 scopes machine learning to perception, and section 5.4
scopes that perception to one job - refining the usable-area estimate the
capacity engine depends on. Nothing here scores hazard, ranks a habitation or
selects a site.

The primary path for usable area remains ESA WorldCover, a published product.
This module trains a small Random Forest on spectral indices from the vendored
Sentinel-2 composite and reports where the two agree. Agreement is a confidence
signal on the usable-area figure, not a claim that either is ground truth.

**Where the labels come from.** Not from WorldCover - a model trained on the
product it is checked against would only prove it can copy. Each class is defined
by a rule over data ASTRA holds for other reasons: the DEM, the D8 hydrology, the
mapped road network and the WorldPop surface. The rules are written out below,
each with the physical reasoning, and the median spectral signature of every
class is recorded when the model is built so a reader can see whether the rule
found what it claimed to.

**What it is.** A 250-tree Random Forest over four indices, trained on a few
thousand rule-labelled pixels in about a second on a CPU. It is not a deep
learning segmentation model and is never described as one.

**What it can and cannot separate.** Vegetation, water and snow separate cleanly
from open ground. Grass, cropland and built-up do not separate reliably from each
other at 30 m - and it does not matter, because all three are buildable, and
buildable-or-not is the only question this model is asked.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MODEL_NAME = "landcover-refinement-rf"
MODEL_VERSION = "2.0.0"

#: Maximum labelled pixels drawn per class, so an abundant class cannot swamp a
#: scarce one. Water and gravel are rare in a mountain corridor; forest is not.
SAMPLES_PER_CLASS = 600

#: Classes whose ground a settlement can be built on. The same distinction the
#: WorldCover reclassification draws, so the two paths answer the same question.
BUILDABLE_LABELS = frozenset({"built", "gravel", "cropland", "grass"})


@dataclass(frozen=True)
class LabelSurfaces:
    """The independent surfaces the labelling rules read, on the composite grid."""

    elevation_m: np.ndarray
    slope_deg: np.ndarray
    upstream_area_km2: np.ndarray
    road_distance_m: np.ndarray
    population_per_km2: np.ndarray


@dataclass(frozen=True)
class LabelRule:
    """A class definition: a rule over independent surfaces, and why it holds."""

    label: str
    reasoning: str
    predicate: Callable[[LabelSurfaces], np.ndarray]
    buildable: bool


TRAINING_RULES: tuple[LabelRule, ...] = (
    LabelRule(
        label="water",
        reasoning=(
            "Cells on the DEM-derived channel network carrying more than 250 km2 of "
            "upstream area on near-flat ground: the Alaknanda and its major "
            "tributaries, which are permanently wet."
        ),
        predicate=lambda s: (s.upstream_area_km2 > 250.0) & (s.slope_deg < 6.0),
        buildable=False,
    ),
    LabelRule(
        label="gravel",
        reasoning=(
            "Flat ground beside smaller channels, 120 to 250 km2 of upstream area: "
            "riverbed gravel and braided bar, bare by definition."
        ),
        predicate=lambda s: (s.upstream_area_km2 > 120.0)
        & (s.upstream_area_km2 <= 250.0)
        & (s.slope_deg < 8.0),
        buildable=True,
    ),
    LabelRule(
        label="snow",
        reasoning=(
            "Steep ground above 4400 m. In a post-monsoon composite this is snow and "
            "ice, and its shortwave signature confirms it."
        ),
        predicate=lambda s: (s.elevation_m > 4400.0) & (s.slope_deg > 30.0),
        buildable=False,
    ),
    LabelRule(
        label="grass",
        reasoning=(
            "Gentle ground between 3200 and 3800 m: above the treeline, below "
            "permanent snow, the alpine grazing belt."
        ),
        predicate=lambda s: (s.elevation_m >= 3200.0)
        & (s.elevation_m <= 3800.0)
        & (s.slope_deg <= 20.0),
        buildable=True,
    ),
    LabelRule(
        label="vegetation",
        reasoning=(
            "Steep slopes between 1900 and 2900 m more than 1.2 km from any mapped "
            "road: the closed-canopy forest belt, too far from access to be cleared."
        ),
        predicate=lambda s: (s.elevation_m >= 1900.0)
        & (s.elevation_m <= 2900.0)
        & (s.slope_deg >= 25.0)
        & (s.road_distance_m > 1200.0),
        buildable=False,
    ),
    LabelRule(
        label="built",
        reasoning=(
            "Gentle ground within 60 m of a mapped road where the WorldPop surface "
            "exceeds 300 people per square kilometre: settlement and roadside."
        ),
        predicate=lambda s: (s.population_per_km2 > 300.0)
        & (s.road_distance_m < 60.0)
        & (s.slope_deg < 20.0),
        buildable=True,
    ),
    LabelRule(
        label="cropland",
        reasoning=(
            "Gentle valley-floor ground between 1000 and 1900 m within 300 m of a "
            "road: the terraced cultivation belt."
        ),
        predicate=lambda s: (s.elevation_m >= 1000.0)
        & (s.elevation_m <= 1900.0)
        & (s.slope_deg <= 14.0)
        & (s.road_distance_m < 300.0),
        buildable=True,
    ),
)


@dataclass(frozen=True)
class SpectralFeatures:
    """Indices computed from the vendored composite, and the mask of valid cells."""

    ndvi: np.ndarray
    ndbi: np.ndarray
    ndwi: np.ndarray
    brightness: np.ndarray
    valid: np.ndarray

    def stack(self) -> np.ndarray:
        return np.stack([self.ndvi, self.ndbi, self.ndwi, self.brightness], axis=-1)

    @staticmethod
    def names() -> list[str]:
        return ["ndvi", "ndbi", "ndwi", "brightness"]


def _ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        result = np.where(np.abs(denominator) > 1e-6, numerator / denominator, 0.0)
    return np.clip(result, -1.0, 1.0)


def spectral_features(bands: dict[str, np.ndarray]) -> SpectralFeatures:
    """NDVI, NDBI, NDWI and brightness from surface reflectance bands."""
    blue = bands["blue"].astype("float64")
    green = bands["green"].astype("float64")
    red = bands["red"].astype("float64")
    nir = bands["nir"].astype("float64")
    swir = bands["swir16"].astype("float64")

    valid = (red > 0) & (nir > 0) & (green > 0) & (swir > 0)
    return SpectralFeatures(
        ndvi=_ratio(nir - red, nir + red),
        ndbi=_ratio(swir - nir, swir + nir),
        ndwi=_ratio(green - nir, green + nir),
        brightness=np.clip((blue + green + red) / 3.0 / 4000.0, 0.0, 1.0),
        valid=valid,
    )


@dataclass
class TrainingSet:
    """Rule-labelled feature rows, with what each rule actually found."""

    features: np.ndarray
    labels: np.ndarray
    label_names: list[str]
    sample_count: int
    per_class: dict[str, int] = field(default_factory=dict)
    signatures: dict[str, dict[str, float]] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


def build_training_set(
    features: SpectralFeatures,
    surfaces: LabelSurfaces,
    *,
    seed: int = 26191,
    samples_per_class: int = SAMPLES_PER_CLASS,
) -> TrainingSet:
    """Sample labelled pixels from each rule, deterministically."""
    rng = np.random.default_rng(seed)
    stack = features.stack()
    rows: list[np.ndarray] = []
    labels: list[str] = []
    per_class: dict[str, int] = {}
    signatures: dict[str, dict[str, float]] = {}
    skipped: list[str] = []

    for rule in TRAINING_RULES:
        mask = rule.predicate(surfaces) & features.valid
        available = int(mask.sum())
        if available == 0:
            skipped.append(f"{rule.label}: no cell in the corridor matches the rule")
            continue
        indices = np.flatnonzero(mask.ravel())
        if available > samples_per_class:
            indices = rng.choice(indices, size=samples_per_class, replace=False)
        sampled = stack.reshape(-1, stack.shape[-1])[indices]
        rows.append(sampled)
        labels.extend([rule.label] * len(indices))
        per_class[rule.label] = len(indices)
        signatures[rule.label] = {
            "available_cells": available,
            "median_ndvi": round(float(np.median(sampled[:, 0])), 4),
            "median_ndbi": round(float(np.median(sampled[:, 1])), 4),
            "median_ndwi": round(float(np.median(sampled[:, 2])), 4),
            "median_brightness": round(float(np.median(sampled[:, 3])), 4),
        }

    if not rows:
        return TrainingSet(
            features=np.empty((0, 4)),
            labels=np.empty((0,), dtype=object),
            label_names=[],
            sample_count=0,
            skipped=skipped,
        )

    matrix = np.concatenate(rows, axis=0)
    label_array = np.array(labels, dtype=object)
    return TrainingSet(
        features=matrix,
        labels=label_array,
        label_names=sorted(set(labels)),
        sample_count=len(label_array),
        per_class=per_class,
        signatures=signatures,
        skipped=skipped,
    )


@dataclass
class RefinementModel:
    """A trained refinement model, with the numbers that say how good it is."""

    classifier: object
    label_names: list[str]
    cross_validated_accuracy: float
    buildable_accuracy: float
    training_samples: int
    feature_names: list[str]
    feature_importance: dict[str, float]
    model_name: str = MODEL_NAME
    model_version: str = MODEL_VERSION

    def predict_buildable(self, features: SpectralFeatures) -> np.ndarray:
        """Per-cell buildable mask from the refinement model."""
        stack = features.stack()
        flat = stack.reshape(-1, stack.shape[-1])
        predictions = np.asarray(self.classifier.predict(flat)).reshape(stack.shape[:2])
        return np.isin(predictions, list(BUILDABLE_LABELS)) & features.valid

    def predict_classes(self, features: SpectralFeatures) -> np.ndarray:
        stack = features.stack()
        flat = stack.reshape(-1, stack.shape[-1])
        return np.asarray(self.classifier.predict(flat)).reshape(stack.shape[:2])


def train_refinement_model(training: TrainingSet, *, seed: int = 26191) -> RefinementModel:
    """Fit the Random Forest and cross-validate it. CPU only, about a second."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import StratifiedKFold, cross_val_predict, cross_val_score

    if training.sample_count == 0:
        raise ValueError("no labelled samples: the composite may not cover the corridor")

    classifier = RandomForestClassifier(
        n_estimators=250,
        max_depth=10,
        min_samples_leaf=3,
        class_weight="balanced",
        random_state=seed,
        n_jobs=1,
    )
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    scores = cross_val_score(
        classifier, training.features, training.labels, cv=folds, scoring="accuracy"
    )

    # The class label is not the decision. What the capacity engine consumes is
    # buildable or not, so that is scored separately - and it is the number that
    # should be quoted.
    predicted = cross_val_predict(
        classifier, training.features, training.labels, cv=folds
    )
    buildable_truth = np.isin(training.labels, list(BUILDABLE_LABELS))
    buildable_predicted = np.isin(predicted, list(BUILDABLE_LABELS))
    buildable_accuracy = float((buildable_truth == buildable_predicted).mean())

    classifier.fit(training.features, training.labels)
    importance = dict(
        zip(
            SpectralFeatures.names(),
            [float(value) for value in classifier.feature_importances_],
            strict=True,
        )
    )
    return RefinementModel(
        classifier=classifier,
        label_names=training.label_names,
        cross_validated_accuracy=float(np.mean(scores)),
        buildable_accuracy=buildable_accuracy,
        training_samples=training.sample_count,
        feature_names=SpectralFeatures.names(),
        feature_importance=importance,
    )


def agreement(primary: np.ndarray, refinement: np.ndarray, valid: np.ndarray) -> float:
    """Share of valid cells where the two land-cover paths agree."""
    mask = valid & np.isfinite(primary) & np.isfinite(refinement)
    if not mask.any():
        return 0.0
    return float(((primary > 0.5) == (refinement > 0.5))[mask].mean())


def read_composite(path: Path) -> dict[str, np.ndarray]:
    """Read the vendored Sentinel-2 composite into named bands."""
    import rasterio

    from astra.data.connectors.imagery import BANDS

    with rasterio.open(path) as source:
        return {
            band: source.read(index + 1).astype("float64")
            for index, band in enumerate(BANDS)
        }


def rule_table() -> list[dict[str, object]]:
    """The labelling rules, for the transparency panel."""
    return [
        {
            "label": rule.label,
            "buildable": rule.buildable,
            "reasoning": rule.reasoning,
        }
        for rule in TRAINING_RULES
    ]
