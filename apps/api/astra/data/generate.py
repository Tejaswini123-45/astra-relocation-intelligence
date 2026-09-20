"""Generation of the synthetic habitation and candidate-site layer.

CLAUDE.md section 4.1: terrain, rivers, roads, elevation and incident history are
real; habitations and candidate sites are synthetic, fictional and
terrain-calibrated, so ASTRA never renders a hazard classification over a real
named village.

"Terrain-calibrated" is meant literally here. Placement is decided by the real
derived surfaces - slope, land cover, elevation band, distance to the mapped road
network, height above nearest drainage - and settlement size is integrated from
the real WorldPop population surface at that location. What is assumed rather
than measured is the demographic composition and the service supply at each
site, and every one of those assumptions is a DEMO_CONFIG constant visible in the
transparency panel.

Generation is deterministic: one seed, in the config, produces one dataset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from astra.data.rasters import RasterLayer
from astra.domain.enums import ProvenanceClass, ServiceType, StructureType
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.models import (
    CandidateSite,
    CriticalFacility,
    DemographicProfile,
    GeoPoint,
    Habitation,
    ServiceSupply,
    StudyArea,
)

# Fictional names. Deliberately not the name of any settlement in the corridor.
HABITATION_NAMES = (
    "Devgarh Tok",
    "Naulkhet",
    "Rauligaon",
    "Bansera Tok",
    "Kandara Gaon",
    "Simalkot",
    "Thalgaon Sera",
    "Dungri Tok",
    "Kharsoli Gaon",
    "Bhairaunkhal",
    "Panduri Sera",
    "Sarauli Tok",
)

SITE_NAMES = (
    "Naulkhet Bench",
    "Kandara Terrace",
    "Upper Simalkot Flat",
    "Bhairaun Plateau",
    "Panduri Terrace",
    "Sarauli Bench",
)


@dataclass(frozen=True)
class SurfaceStack:
    """The derived surfaces the generator reads. All real, all computed offline."""

    elevation: RasterLayer
    slope: RasterLayer
    hand: RasterLayer
    road_distance: RasterLayer
    drainage_distance: RasterLayer
    buildable: RasterLayer
    population_density: RasterLayer

    @property
    def shape(self) -> tuple[int, int]:
        return self.elevation.data.shape  # type: ignore[return-value]


@dataclass(frozen=True)
class PlacedPoint:
    """A chosen location with the terrain values that justified choosing it."""

    lon: float
    lat: float
    row: int
    col: int
    elevation_m: float
    slope_deg: float
    hand_m: float
    road_distance_m: float
    drainage_distance_m: float
    population_per_km2: float


def _coordinates(layer: RasterLayer, row: int, col: int) -> tuple[float, float]:
    lon, lat = layer.transform * (col + 0.5, row + 0.5)
    return float(lon), float(lat)


def _settlement_suitability(stack: SurfaceStack) -> np.ndarray:
    """Where a hill settlement plausibly sits, from the real surfaces only.

    This is not a safety score. Real settlements in this corridor sit on
    road-linked benches at habitable elevations, some of them in places ASTRA
    will later classify as hazardous - which is the entire point of the problem
    statement.
    """
    config = MODEL_CONFIG.generation
    elevation = stack.elevation.data
    slope = stack.slope.data
    road_distance = stack.road_distance.data

    in_band = (elevation >= config.settlement_min_elevation_m.value) & (
        elevation <= config.settlement_max_elevation_m.value
    )
    road_linked = road_distance <= config.settlement_max_road_distance_m.value
    buildable = stack.buildable.data > 0.5
    habitable_slope = slope <= 38.0

    feasible = in_band & road_linked & buildable & habitable_slope

    # Preference within the feasible set: gentler ground, closer to a road, and
    # where people actually are according to the population surface.
    slope_pref = np.clip(1.0 - slope / 38.0, 0.0, 1.0)
    road_pref = np.clip(
        1.0 - road_distance / config.settlement_max_road_distance_m.value, 0.0, 1.0
    )
    density = stack.population_density.data
    density_pref = np.clip(density / max(np.nanpercentile(density, 95), 1.0), 0.0, 1.0)

    score = 0.40 * slope_pref + 0.25 * road_pref + 0.35 * density_pref
    return np.where(feasible, score, -1.0)


def _stratified_pick(
    stack: SurfaceStack,
    score: np.ndarray,
    count: int,
    min_separation_m: float,
    stratify_by: np.ndarray,
    strata: int = 4,
) -> list[PlacedPoint]:
    """Pick high-scoring cells spread across the terrain-exposure range.

    Taking the top N cells would cluster every habitation on the safest, flattest,
    best-connected ground, which would make the whole exercise trivial and
    dishonest. Instead the feasible cells are divided into bands of the
    stratifying surface and the best candidate is taken from each band in turn.
    """
    cell = stack.elevation.cell_size
    valid = score > 0
    if not valid.any():
        return []

    bands = np.full(score.shape, -1, dtype=int)
    edges = np.nanpercentile(stratify_by[valid], np.linspace(0, 100, strata + 1))
    for index in range(strata):
        low, high = edges[index], edges[index + 1]
        selector = valid & (stratify_by >= low) & (
            stratify_by <= high if index == strata - 1 else stratify_by < high
        )
        bands[selector] = index

    chosen: list[PlacedPoint] = []
    order = np.argsort(score, axis=None)[::-1]
    per_band = {index: 0 for index in range(strata)}
    target_per_band = count / strata

    for flat_index in order:
        if len(chosen) >= count:
            break
        row, col = np.unravel_index(flat_index, score.shape)
        if score[row, col] <= 0:
            break
        band = bands[row, col]
        if band < 0:
            continue
        if per_band[band] >= np.ceil(target_per_band):
            continue
        lon, lat = _coordinates(stack.elevation, int(row), int(col))
        too_close = any(
            np.hypot(
                (lon - point.lon) * cell.x_m / abs(stack.elevation.transform.a),
                (lat - point.lat) * cell.y_m / abs(stack.elevation.transform.e),
            )
            < min_separation_m
            for point in chosen
        )
        if too_close:
            continue
        per_band[band] += 1
        chosen.append(
            PlacedPoint(
                lon=lon,
                lat=lat,
                row=int(row),
                col=int(col),
                elevation_m=float(stack.elevation.data[row, col]),
                slope_deg=float(stack.slope.data[row, col]),
                hand_m=float(stack.hand.data[row, col]),
                road_distance_m=float(stack.road_distance.data[row, col]),
                drainage_distance_m=float(stack.drainage_distance.data[row, col]),
                population_per_km2=float(stack.population_density.data[row, col]),
            )
        )
    return chosen


def _population_for(point: PlacedPoint) -> int:
    """Integrate the real population surface over the settlement catchment."""
    config = MODEL_CONFIG.generation
    density = max(point.population_per_km2, 0.0)
    raw = density * config.settlement_catchment_km2.value
    return int(
        np.clip(round(raw), config.population_min.value, config.population_max.value)
    )


def _remoteness(point: PlacedPoint) -> float:
    """0 for the best-connected habitation, 1 for the least connected."""
    config = MODEL_CONFIG.generation
    road = np.clip(
        point.road_distance_m / config.settlement_max_road_distance_m.value, 0.0, 1.0
    )
    elevation_span = (
        config.settlement_max_elevation_m.value - config.settlement_min_elevation_m.value
    )
    height = np.clip(
        (point.elevation_m - config.settlement_min_elevation_m.value) / elevation_span,
        0.0,
        1.0,
    )
    return float(0.6 * road + 0.4 * height)


def _demographics(
    population: int, households: int, rng: np.random.Generator
) -> DemographicProfile:
    config = MODEL_CONFIG.generation
    jitter = config.demographic_jitter.value

    def share(base: float) -> float:
        return float(np.clip(base * (1.0 + rng.uniform(-jitter, jitter)), 0.0, 1.0))

    return DemographicProfile(
        elderly_60_plus=int(round(population * share(config.share_elderly.value))),
        children_under_5=int(round(population * share(config.share_children_u5.value))),
        persons_with_disability=int(
            round(population * share(config.share_disability.value))
        ),
        medically_dependent=int(
            round(population * share(config.share_medical_dependency.value))
        ),
        low_income_households=int(
            round(households * share(config.share_low_income_households.value))
        ),
    )


def _structure_mix(remoteness: float) -> dict[StructureType, float]:
    config = MODEL_CONFIG.generation
    kutcha = float(
        config.kutcha_share_connected.value
        + remoteness
        * (config.kutcha_share_remote.value - config.kutcha_share_connected.value)
    )
    semi_pucca = float(np.clip(0.34 + 0.10 * remoteness, 0.0, 1.0 - kutcha))
    pucca = round(1.0 - kutcha - semi_pucca, 6)
    return {
        StructureType.KUTCHA: round(kutcha, 6),
        StructureType.SEMI_PUCCA: round(semi_pucca, 6),
        StructureType.PUCCA: pucca,
    }


def _facilities(
    habitation_id: str, population: int, point: PlacedPoint, rng: np.random.Generator
) -> list[CriticalFacility]:
    """Facility provision scaled to settlement size, placed inside the footprint."""
    facilities: list[CriticalFacility] = []
    offsets = rng.uniform(-0.0025, 0.0025, size=(3, 2))

    def place(index: int) -> GeoPoint:
        return GeoPoint(
            lon=round(point.lon + float(offsets[index][0]), 6),
            lat=round(point.lat + float(offsets[index][1]), 6),
        )

    if population >= 150:
        facilities.append(
            CriticalFacility(
                id=f"{habitation_id}-F1",
                kind="ANGANWADI",
                name="Anganwadi centre",
                location=place(0),
            )
        )
    if population >= 260:
        facilities.append(
            CriticalFacility(
                id=f"{habitation_id}-F2",
                kind="SCHOOL",
                name="Primary school",
                location=place(1),
            )
        )
    if population >= 620:
        facilities.append(
            CriticalFacility(
                id=f"{habitation_id}-F3",
                kind="CLINIC",
                name="Sub-centre clinic",
                location=place(2),
            )
        )
    return facilities


def generate_habitations(stack: SurfaceStack, area: StudyArea) -> list[Habitation]:
    """Twelve synthetic habitations, placed and sized from the real surfaces."""
    config = MODEL_CONFIG.generation
    rng = np.random.default_rng(int(config.seed.value))
    score = _settlement_suitability(stack)
    points = _stratified_pick(
        stack,
        score,
        count=int(config.habitation_count.value),
        min_separation_m=config.min_separation_m.value,
        stratify_by=stack.hand.data,
    )

    # The market town every habitation trades with: the densest populated cell in
    # the corridor, taken from the real population surface.
    density = stack.population_density.data
    market_row, market_col = np.unravel_index(
        int(np.nanargmax(np.nan_to_num(density, nan=0.0))), density.shape
    )
    market_lon, market_lat = _coordinates(
        stack.population_density, int(market_row), int(market_col)
    )

    habitations: list[Habitation] = []
    for index, point in enumerate(points):
        population = _population_for(point)
        households = max(
            1,
            int(round(population / MODEL_CONFIG.capacity.persons_per_household.value)),
        )
        remoteness = _remoteness(point)
        identifier = f"H-{index + 1:02d}"
        habitations.append(
            Habitation(
                id=identifier,
                name=HABITATION_NAMES[index % len(HABITATION_NAMES)],
                centroid=GeoPoint(lon=round(point.lon, 6), lat=round(point.lat, 6)),
                population=population,
                households=households,
                demographics=_demographics(population, households, rng),
                structure_mix=_structure_mix(remoteness),
                critical_facilities=_facilities(identifier, population, point, rng),
                livelihood_centre=GeoPoint(
                    lon=round(market_lon, 6), lat=round(market_lat, 6)
                ),
                elevation_m=round(point.elevation_m, 1),
                block="Alaknanda corridor (demonstration)",
                district=area.district,
                state=area.state,
                provenance=ProvenanceClass.SYNTHETIC_CALIBRATED,
            )
        )
    return habitations


def _site_suitability(stack: SurfaceStack) -> np.ndarray:
    """Where a relocation site could plausibly be built.

    Stricter than settlement suitability: gentle ground, buildable land cover,
    well above the nearest channel, and reachable by road. The formal suitability
    gates are applied later by the capacity engine against the hazard surface;
    this only decides where candidates are proposed at all.
    """
    slope = stack.slope.data
    hand = stack.hand.data
    road_distance = stack.road_distance.data
    elevation = stack.elevation.data
    buildable = stack.buildable.data > 0.5

    feasible = (
        (slope <= 15.0)
        & (hand >= 25.0)
        & (road_distance <= 1200.0)
        & buildable
        & (elevation >= 900.0)
        & (elevation <= 2400.0)
    )
    slope_pref = np.clip(1.0 - slope / 15.0, 0.0, 1.0)
    hand_pref = np.clip(hand / 120.0, 0.0, 1.0)
    road_pref = np.clip(1.0 - road_distance / 1200.0, 0.0, 1.0)
    score = 0.45 * slope_pref + 0.30 * hand_pref + 0.25 * road_pref
    return np.where(feasible, score, -1.0)


def _service_supply(
    site_index: int, gross_area_m2: float, rng: np.random.Generator
) -> list[ServiceSupply]:
    """Assumed service infrastructure at a candidate site.

    Declared synthetic. The point of varying supply across sites is that each site
    ends up with a different binding constraint, which is what the capacity engine
    exists to find - not to flatter any particular site.
    """
    capacity = MODEL_CONFIG.capacity
    nominal_persons = gross_area_m2 / capacity.site_area_m2_per_person.value

    # Each service is provisioned to some fraction of what the land could hold.
    factors = {
        ServiceType.WATER: (0.55, 1.30),
        ServiceType.SANITATION: (0.45, 1.20),
        ServiceType.HEALTHCARE: (0.60, 1.60),
        ServiceType.POWER: (0.70, 1.50),
        ServiceType.SHELTER: (0.30, 0.85),
    }
    supplies: list[ServiceSupply] = []
    for service, (low, high) in factors.items():
        factor = float(rng.uniform(low, high))
        served = max(nominal_persons * factor, 0.0)
        if service is ServiceType.WATER:
            supply = served * capacity.water_litres_per_person_day.value
            unit = "L/day"
        elif service is ServiceType.SANITATION:
            supply = max(1.0, round(served / capacity.persons_per_latrine.value))
            unit = "latrine units"
        elif service is ServiceType.HEALTHCARE:
            supply = served / capacity.persons_per_health_facility.value
            unit = "facility units"
        elif service is ServiceType.POWER:
            households = served / capacity.persons_per_household.value
            supply = households * capacity.power_kva_per_household.value
            unit = "kVA"
        else:
            supply = max(1.0, round(served / capacity.shelter_occupancy_persons_per_unit.value))
            unit = "shelter units"
        supplies.append(
            ServiceSupply(
                service=service,
                supply=round(float(supply), 2),
                unit=unit,
                provenance=ProvenanceClass.SYNTHETIC_CALIBRATED,
                source_note=(
                    "Assumed existing provision at this candidate site. Would be "
                    "replaced by an SDMA field survey."
                ),
            )
        )
    return supplies


def generate_sites(stack: SurfaceStack, area: StudyArea) -> list[CandidateSite]:
    """Six synthetic candidate relocation sites on genuinely buildable ground."""
    config = MODEL_CONFIG.generation
    rng = np.random.default_rng(int(config.seed.value) + 1)
    score = _site_suitability(stack)
    points = _stratified_pick(
        stack,
        score,
        count=int(config.site_count.value),
        min_separation_m=2500.0,
        stratify_by=stack.elevation.data,
        strata=3,
    )

    sites: list[CandidateSite] = []
    for index, point in enumerate(points):
        # Site extent is the contiguous gentle, buildable ground around the point,
        # measured on the real surfaces rather than assumed.
        gross_area_m2 = _contiguous_buildable_area(stack, point)
        identifier = f"S-{index + 1:02d}"
        existing_units = int(rng.integers(0, 26))
        # A dwelling needs a plot, not just a footprint: the Sphere site-area norm
        # times household size is the land one household actually occupies once
        # access, services and setback are included. Dividing by the dwelling
        # footprint alone would claim thousands of units on a few hectares.
        land_per_household_m2 = (
            MODEL_CONFIG.capacity.site_area_m2_per_person.value
            * MODEL_CONFIG.capacity.persons_per_household.value
        )
        constructable = int(gross_area_m2 * 0.35 / land_per_household_m2)
        sites.append(
            CandidateSite(
                id=identifier,
                name=SITE_NAMES[index % len(SITE_NAMES)],
                centroid=GeoPoint(lon=round(point.lon, 6), lat=round(point.lat, 6)),
                gross_area_m2=round(gross_area_m2, 1),
                mean_slope_deg=round(point.slope_deg, 2),
                elevation_m=round(point.elevation_m, 1),
                distance_to_road_m=round(point.road_distance_m, 1),
                existing_shelter_units=existing_units,
                constructable_units=constructable,
                services=_service_supply(index, gross_area_m2, rng),
                district=area.district,
                state=area.state,
                provenance=ProvenanceClass.SYNTHETIC_CALIBRATED,
                notes=(
                    "Candidate extent measured as contiguous buildable ground under "
                    "15 degrees slope around the chosen point. Land ownership, tenure "
                    "and encumbrance are not verified."
                ),
            )
        )
    return sites


def _contiguous_buildable_area(stack: SurfaceStack, point: PlacedPoint) -> float:
    """Measure the buildable patch a site actually occupies.

    Only the connected patch containing the site centre counts, measured with the
    capacity engine's own window and thresholds. Summing every scrap of gentle
    ground within half a kilometre would inflate the site, and the engine would
    then find far less land than the site claims to have.
    """
    from scipy import ndimage

    capacity = MODEL_CONFIG.capacity
    cell = stack.elevation.cell_size
    rows, cols = stack.shape
    # The same window, the same thresholds and the same connectivity rule the
    # capacity engine uses, so a site has exactly one measured area rather than
    # one number in the fixture and a different one on the capacity screen.
    radius_cells_y = max(1, int(round(capacity.site_measure_radius_m.value / cell.y_m)))
    radius_cells_x = max(1, int(round(capacity.site_measure_radius_m.value / cell.x_m)))
    r0, r1 = max(0, point.row - radius_cells_y), min(rows, point.row + radius_cells_y + 1)
    c0, c1 = max(0, point.col - radius_cells_x), min(cols, point.col + radius_cells_x + 1)
    slope_window = stack.slope.data[r0:r1, c0:c1]
    buildable_window = stack.buildable.data[r0:r1, c0:c1] > 0.5
    hand_window = stack.hand.data[r0:r1, c0:c1]
    usable = (
        (slope_window <= capacity.gate_max_slope_deg.value)
        & buildable_window
        & (hand_window >= capacity.gate_min_hand_m.value)
    )
    if not usable.any():
        return 0.0

    labelled, count = ndimage.label(usable, structure=np.ones((3, 3), dtype=int))
    if count == 0:
        return 0.0
    centre = (point.row - r0, point.col - c0)
    label = int(labelled[centre])
    if label == 0:
        sizes = np.bincount(labelled.ravel())
        sizes[0] = 0
        label = int(sizes.argmax())
    return float((labelled == label).sum() * cell.area_m2)


def generation_manifest(
    habitations: list[Habitation], sites: list[CandidateSite], area: StudyArea
) -> dict[str, Any]:
    """What was generated, from what, and what was assumed rather than measured."""
    config = MODEL_CONFIG.generation
    return {
        "study_area": area.id,
        "seed": int(config.seed.value),
        "model_config_version": MODEL_CONFIG.version,
        "provenance": ProvenanceClass.SYNTHETIC_CALIBRATED.value,
        "measured_from_real_data": [
            "placement: Copernicus DEM slope, elevation band, height above nearest "
            "drainage, ESA WorldCover buildable classes, distance to the OSM road network",
            "population: WorldPop 2020 UN-adjusted 1 km population surface integrated "
            f"over a {config.settlement_catchment_km2.value} km2 settlement catchment",
            "site extent: contiguous buildable ground under 15 degrees slope measured "
            "on the real surfaces",
            "livelihood centre: densest populated cell in the corridor on the WorldPop "
            "surface",
        ],
        "assumed_not_measured": [
            "demographic composition (elderly, under-five, disability, medical "
            "dependency and low-income household shares) - DEMO_CONFIG assumptions for "
            "a Himalayan hill district, not census values",
            "dwelling typology mix, scaled by road remoteness",
            "service infrastructure supply at each candidate site",
            "critical facility provision thresholds by settlement size",
        ],
        "totals": {
            "habitations": len(habitations),
            "population": sum(h.population for h in habitations),
            "households": sum(h.households for h in habitations),
            "sites": len(sites),
            "site_gross_area_m2": round(sum(s.gross_area_m2 for s in sites), 1),
        },
        "names": {
            "note": (
                "All habitation and site names are fictional and were chosen not to "
                "match any settlement in the corridor."
            ),
            "habitations": [h.name for h in habitations],
            "sites": [s.name for s in sites],
        },
    }
