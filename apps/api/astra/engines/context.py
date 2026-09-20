"""Assembling the engine inputs from vendored and derived data.

The engines themselves take arrays and know nothing about files. This module is
the seam: it reads the local artifacts once, aggregates them onto the analysis
grid, turns the incident record and the rainfall archive into surfaces, and
caches the result.

Nothing here touches the network. Everything it reads was vendored by
``scripts/ingest.py`` and computed by ``scripts/build_derived.py``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import numpy as np

from astra.data.fixtures import FixtureBundle, load_fixtures
from astra.data.rasters import read_raster
from astra.data.study_area import get_study_area
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.models import StudyArea
from astra.engines.grid import AnalysisGrid
from astra.engines.hazard import HazardSurfaces, kernel_density
from astra.settings import get_settings

#: Stored scale factors, matching how build_derived.py wrote each layer.
DERIVED_SCALES: dict[str, float] = {
    "slope_deg": 0.5,
    "ruggedness": 0.1,
    "hand_m": 0.1,
    "drainage_distance_m": 1.0,
    "drainage_density": 0.1,
    "catchment_slope_deg": 0.5,
    "upstream_area_km2": 0.1,
    "confluence_density": 0.1,
    "landcover_instability": 0.01,
    "landcover_infiltration": 0.01,
    "landcover_buildable": 1.0,
    "road_distance_m": 1.0,
    "waterway_distance_m": 1.0,
    "drainage": 1.0,
    "hillshade": 1.0,
}


class EngineDataError(RuntimeError):
    """Raised when an engine input has not been built yet."""


@dataclass(frozen=True)
class IncidentRecord:
    """One recorded historical incident, as ASTRA uses it."""

    lon: float
    lat: float
    weight: float
    age_years: float
    size: str
    category: str
    trigger: str
    date: str
    fatalities: int


def load_incidents(
    path: Path | None = None, *, today: datetime | None = None
) -> list[IncidentRecord]:
    """Parse the vendored landslide inventory into weighted, dated points.

    Records whose stated location accuracy is coarser than the configured limit
    are dropped rather than smeared across the grid.
    """
    settings = get_settings()
    source = path or settings.raw_dir / "incidents" / "nasa_glc_uttarakhand.csv"
    if not source.exists():
        raise EngineDataError(
            f"incident inventory missing at {source}; run scripts/ingest.py"
        )
    config = MODEL_CONFIG.evidence
    weights = {
        "small": config.incident_weight_small.value,
        "medium": config.incident_weight_medium.value,
        "large": config.incident_weight_large.value,
        "very_large": config.incident_weight_very_large.value,
    }
    now = today or datetime.now(UTC)

    records: list[IncidentRecord] = []
    with source.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                lon = float(row["longitude"])
                lat = float(row["latitude"])
            except (KeyError, TypeError, ValueError):
                continue

            accuracy = (row.get("location_accuracy") or "unknown").strip().lower()
            if accuracy.endswith("km"):
                try:
                    if float(accuracy[:-2]) > config.incident_max_location_error_km.value:
                        continue
                except ValueError:
                    pass

            size = (row.get("landslide_size") or "unknown").strip().lower()
            weight = weights.get(size, config.incident_weight_unknown.value)
            try:
                fatalities = int(float(row.get("fatality_count") or 0))
            except ValueError:
                fatalities = 0
            weight += min(
                fatalities * config.incident_fatality_bonus.value,
                config.incident_fatality_bonus_cap.value,
            )

            raw_date = (row.get("event_date") or "").strip()
            age_years = 15.0
            for pattern in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(raw_date, pattern).replace(tzinfo=UTC)
                except ValueError:
                    continue
                age_years = max((now - parsed).days / 365.25, 0.0)
                break

            records.append(
                IncidentRecord(
                    lon=lon,
                    lat=lat,
                    weight=weight,
                    age_years=age_years,
                    size=size,
                    category=(row.get("landslide_category") or "unknown").strip(),
                    trigger=(row.get("landslide_trigger") or "unknown").strip(),
                    date=raw_date[:10],
                    fatalities=fatalities,
                )
            )
    return records


@dataclass(frozen=True)
class RainfallStation:
    """One ERA5 grid point, reduced to the two indices the hazard model uses."""

    lon: float
    lat: float
    mean_annual_max_daily_mm: float
    heavy_rain_days_per_year: float
    years: float
    wettest_day_mm: float


def load_rainfall(path: Path | None = None) -> list[RainfallStation]:
    """Reduce the vendored daily precipitation archive to per-point indices."""
    import json

    settings = get_settings()
    source = path or settings.raw_dir / "rainfall" / "open_meteo_era5_alaknanda.json"
    if not source.exists():
        raise EngineDataError(
            f"rainfall archive missing at {source}; run scripts/ingest.py"
        )
    threshold = MODEL_CONFIG.normalisation.extreme_rain_threshold_mm.value
    payload = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = [payload]

    stations: list[RainfallStation] = []
    for point in payload:
        daily = point.get("daily", {})
        dates = daily.get("time", [])
        values = np.array(
            [0.0 if value is None else float(value) for value in daily.get("precipitation_sum", [])]
        )
        if values.size == 0:
            continue
        years = np.array([int(str(date)[:4]) for date in dates])
        annual_max = [
            float(values[years == year].max()) for year in np.unique(years) if (years == year).any()
        ]
        heavy_days = float((values >= threshold).sum())
        span_years = max(len(np.unique(years)), 1)
        stations.append(
            RainfallStation(
                lon=float(point["longitude"]),
                lat=float(point["latitude"]),
                mean_annual_max_daily_mm=float(np.mean(annual_max)) if annual_max else 0.0,
                heavy_rain_days_per_year=heavy_days / span_years,
                years=float(span_years),
                wettest_day_mm=float(values.max()),
            )
        )
    return stations


def interpolate_stations(
    grid: AnalysisGrid, stations: list[RainfallStation], values: np.ndarray
) -> np.ndarray:
    """Inverse-distance interpolation of station values across the grid."""
    if not stations:
        return np.zeros(grid.shape)
    power = MODEL_CONFIG.evidence.rainfall_idw_power.value
    lon_grid, lat_grid = grid.centres()
    numerator = np.zeros(grid.shape)
    denominator = np.zeros(grid.shape)
    for station, value in zip(stations, values, strict=True):
        dx = (lon_grid - station.lon) * grid.cell.x_m / abs(grid.transform.a)
        dy = (lat_grid - station.lat) * grid.cell.y_m / abs(grid.transform.e)
        distance = np.hypot(dx, dy)
        distance = np.maximum(distance, 1.0)
        weight = distance ** (-power)
        numerator += weight * value
        denominator += weight
    return numerator / denominator


@dataclass
class EngineContext:
    """Everything the engines need for one study area, loaded once."""

    study_area: StudyArea
    grid: AnalysisGrid
    surfaces: HazardSurfaces
    incidents: list[IncidentRecord]
    rainfall: list[RainfallStation]
    fixtures: FixtureBundle

    @property
    def habitations(self):
        return self.fixtures.habitations

    @property
    def sites(self):
        return self.fixtures.sites


def _read_derived(name: str, derived_dir: Path):
    path = derived_dir / f"{name}.tif"
    if not path.exists():
        raise EngineDataError(
            f"derived layer '{name}' missing at {path}; run scripts/build_derived.py"
        )
    return read_raster(path, name=name, scale=DERIVED_SCALES.get(name, 1.0))


def build_context(study_area_id: str | None = None) -> EngineContext:
    """Assemble the engine context from local artifacts."""
    settings = get_settings()
    area = get_study_area(study_area_id)
    grid = AnalysisGrid.from_bbox(
        area.bbox, MODEL_CONFIG.hazard.grid_resolution_m.value
    )
    derived = settings.derived_dir

    def aggregate(name: str, how: str = "mean") -> np.ndarray:
        return grid.aggregate(_read_derived(name, derived), how=how)  # type: ignore[arg-type]

    incidents = load_incidents()
    stations = load_rainfall()

    incident_lons = np.array([record.lon for record in incidents])
    incident_lats = np.array([record.lat for record in incidents])
    incident_weights = np.array([record.weight for record in incidents])
    incident_ages = np.array([record.age_years for record in incidents])

    bandwidth = MODEL_CONFIG.hazard.history_kernel_radius_m.value
    incident_density = kernel_density(
        grid, incident_lons, incident_lats, incident_weights, bandwidth
    )
    age_density = kernel_density(
        grid, incident_lons, incident_lats, incident_weights * incident_ages, bandwidth
    )
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_age = np.where(incident_density > 1e-9, age_density / incident_density, np.nan)

    rainfall_intensity = interpolate_stations(
        grid,
        stations,
        np.array([station.mean_annual_max_daily_mm for station in stations]),
    )
    extreme_days = interpolate_stations(
        grid,
        stations,
        np.array([station.heavy_rain_days_per_year for station in stations]),
    )

    surfaces = HazardSurfaces(
        grid=grid,
        slope_deg=aggregate("slope_deg"),
        ruggedness_m=aggregate("ruggedness"),
        drainage_density=aggregate("drainage_density"),
        hand_m=aggregate("hand_m"),
        drainage_distance_m=aggregate("drainage_distance_m"),
        catchment_slope_deg=aggregate("catchment_slope_deg"),
        upstream_area_km2=aggregate("upstream_area_km2", how="max"),
        confluence_density=aggregate("confluence_density"),
        landcover_instability=aggregate("landcover_instability"),
        landcover_infiltration=aggregate("landcover_infiltration"),
        incident_density=incident_density,
        rainfall_intensity_mm=rainfall_intensity,
        extreme_rain_days=extreme_days,
        evidence_recency_years=mean_age,
    )

    return EngineContext(
        study_area=area,
        grid=grid,
        surfaces=surfaces,
        incidents=incidents,
        rainfall=stations,
        fixtures=load_fixtures(),
    )


@lru_cache(maxsize=2)
def get_context(study_area_id: str | None = None) -> EngineContext:
    """Process-wide cached context. Inputs are immutable between builds."""
    return build_context(study_area_id)
