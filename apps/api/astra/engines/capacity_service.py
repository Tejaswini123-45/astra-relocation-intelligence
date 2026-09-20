"""Wiring Engine 4 to the real surfaces.

The capacity engine takes arrays and numbers so it can be tested against
arithmetic. This module is what hands it the corridor: the land-cover, slope and
drainage rasters at their native resolution, the classified hazard surface, and
the distance from each candidate site to the nearest zone ASTRA considers unsafe.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy import ndimage

from astra.data.rasters import RasterLayer, read_raster
from astra.domain.enums import ServiceType, ZoneClass
from astra.domain.model_config import MODEL_CONFIG
from astra.engines.capacity import (
    PENDING_ACCESS,
    CapacityEngine,
    SiteCapacity,
)
from astra.engines.context import DERIVED_SCALES
from astra.engines.hazard import ZONE_ORDER
from astra.engines.service import RiskRun, baseline_risk
from astra.settings import get_settings


@dataclass(frozen=True)
class CapacitySurfaces:
    """The native-resolution surfaces the capacity engine measures on."""

    slope: RasterLayer
    hand: RasterLayer
    buildable: RasterLayer
    #: The Random Forest second opinion on buildable ground. Optional: if it has
    #: not been built, usable area is measured from the published product alone
    #: and the response says the refinement was not available.
    refinement: RasterLayer | None = None


@lru_cache(maxsize=1)
def capacity_surfaces() -> CapacitySurfaces:
    derived = get_settings().derived_dir
    refinement_path = derived / "landcover_ml_buildable.tif"
    return CapacitySurfaces(
        slope=read_raster(
            derived / "slope_deg.tif", name="slope", scale=DERIVED_SCALES["slope_deg"]
        ),
        hand=read_raster(
            derived / "hand_m.tif", name="hand", scale=DERIVED_SCALES["hand_m"]
        ),
        buildable=read_raster(derived / "landcover_buildable.tif", name="buildable"),
        refinement=(
            read_raster(refinement_path, name="refinement")
            if refinement_path.exists()
            else None
        ),
    )


def _unsafe_distance_surface(run: RiskRun) -> np.ndarray:
    """Distance in metres from every analysis cell to the nearest unsafe zone.

    "Unsafe" here means Elevated or Critical: the classes a relocation site has
    to stand clear of, with the configured buffer.
    """
    elevated_ordinal = ZONE_ORDER.index(ZoneClass.ELEVATED)
    unsafe = run.result.zone_class >= elevated_ordinal
    if not unsafe.any():
        return np.full(run.grid.shape, np.inf)
    cell = run.grid.cell
    distance = ndimage.distance_transform_edt(~unsafe, sampling=(cell.y_m, cell.x_m))
    return np.asarray(distance, dtype="float64")


def compute_site_capacity(
    run: RiskRun | None = None, access: dict[str, float] | None = None
) -> list[SiteCapacity]:
    """Run Engine 4 over every candidate site in the scenario.

    ``access`` maps a site id to the number of people the roads reaching it can
    deliver, from Engine 5. Omitting it leaves access declared pending rather
    than assumed unconstrained, which is what happens if the OSM extract is not
    vendored.
    """
    run = run or baseline_risk()
    surfaces = capacity_surfaces()
    engine = CapacityEngine()
    radius = MODEL_CONFIG.capacity.site_measure_radius_m.value
    unsafe_distance = _unsafe_distance_surface(run)

    results: list[SiteCapacity] = []
    for site in run.context.sites:
        lon, lat = site.centroid.lon, site.centroid.lat

        slope_window = surfaces.slope.window_array(lon, lat, radius)
        hand_window = surfaces.hand.window_array(lon, lat, radius)
        buildable_window = surfaces.buildable.window_array(lon, lat, radius)
        buildable_resampled = _match_shape(buildable_window, slope_window.shape)

        refinement_agreement: float | None = None
        refinement_note: str | None = None
        if surfaces.refinement is not None:
            refined_window = _match_shape(
                surfaces.refinement.window_array(lon, lat, radius), slope_window.shape
            )
            valid = np.isfinite(buildable_resampled) & np.isfinite(refined_window)
            if valid.any():
                refinement_agreement = float(
                    ((buildable_resampled > 0.5) == (refined_window > 0.5))[valid].mean()
                )
                refinement_note = (
                    "Second opinion from the Sentinel-2 Random Forest refinement: it "
                    f"agrees with the published land-cover product on "
                    f"{refinement_agreement * 100:.0f}% of cells at this site. Usable "
                    "area is measured from the published product; the refinement is a "
                    "confidence signal on it, not a replacement for it."
                )

        usable = engine.usable_area(
            buildable=buildable_resampled,
            slope_deg=slope_window,
            hand_m=hand_window,
            cell_area_m2=surfaces.slope.cell_size.area_m2,
            radius_m=radius,
            refinement_agreement=refinement_agreement,
            refinement_note=refinement_note,
        )

        index = run.grid.index_of(lon, lat)
        if index is None:
            zone_class = ZoneClass.LOW
            distance_to_unsafe = float("inf")
        else:
            zone_class = ZONE_ORDER[int(run.result.zone_class[index])]
            distance_to_unsafe = float(unsafe_distance[index])

        gates = engine.gates(
            site,
            zone_class=zone_class,
            distance_to_hazard_m=distance_to_unsafe,
            mean_slope_deg=usable.footprint_mean_slope_deg,
            buildable_fraction=usable.buildable_fraction,
            hand_m=usable.footprint_mean_hand_m,
        )

        access_capacity = access.get(site.id) if access is not None else None
        services = engine.service_capacities(site, usable.usable_m2, access_capacity)
        effective, bottleneck = engine.effective(services)
        results.append(
            SiteCapacity(
                site=site,
                gates=gates,
                usable_area=usable,
                services=services,
                theoretical_capacity=engine.theoretical(services),
                effective_capacity=effective,
                bottleneck=bottleneck,
                interventions=engine.interventions(site, services),
                pending_constraints=[] if access_capacity is not None else [PENDING_ACCESS],
            )
        )

    # Sites that pass every gate come first, then by what they can absorb. A
    # site that fails a hard gate has no capacity an SDMA can plan against, so
    # it must not head a list ordered by capacity - it is listed, with its named
    # gate failure, below the sites that are actually available.
    results.sort(key=lambda entry: (not entry.suitable, -entry.effective_capacity))
    return results


def _match_shape(source: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Nearest-neighbour resample a finer window onto a coarser one's grid.

    Land cover is 10 m and the terrain surfaces are 30 m; both describe the same
    ground, and the capacity engine needs them cell for cell.
    """
    if source.size == 0:
        return np.full(shape, np.nan)
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


@lru_cache(maxsize=1)
def baseline_capacity() -> list[SiteCapacity]:
    """The cached baseline capacity assessment, warmed at API startup.

    Engine 5 supplies the access constraint. If the road network is unavailable
    the assessment still runs, with access declared pending on every site - a
    capacity figure that says it may be optimistic beats no capacity figure and
    beats a silently optimistic one.
    """
    return compute_site_capacity(access=_baseline_access())


def _baseline_access() -> dict[str, float] | None:
    from astra.engines.routes_service import RouteDataError

    try:
        from astra.engines.routes_service import baseline_routes

        corridor = baseline_routes()
    except RouteDataError:
        return None
    return {
        site_id: entry.access_capacity_persons
        for site_id, entry in corridor.access.items()
    }


def total_effective_capacity(results: list[SiteCapacity]) -> float:
    """Effective capacity across every site that passes its gates."""
    return round(
        sum(entry.effective_capacity for entry in results if entry.suitable), 1
    )


def bottleneck_summary(results: list[SiteCapacity]) -> dict[str, int]:
    """How many sites each service binds. The district-level version of the answer."""
    summary: dict[str, int] = {}
    for entry in results:
        if entry.bottleneck is None:
            continue
        key = entry.bottleneck.value
        summary[key] = summary.get(key, 0) + 1
    return summary


def service_order() -> list[ServiceType]:
    return [
        ServiceType.LAND,
        ServiceType.SHELTER,
        ServiceType.WATER,
        ServiceType.SANITATION,
        ServiceType.HEALTHCARE,
        ServiceType.POWER,
        ServiceType.ACCESS,
    ]
