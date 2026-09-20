"""The single versioned source of every weight, threshold and norm in ASTRA.

CLAUDE.md section 5: "All weights, thresholds and norms live in one versioned
config module, exposed at GET /model/config, and rendered in the UI's
transparency panel. There are no magic numbers inside engine logic."

Two rules govern this file:

1. Every constant carries a :class:`ProvenanceClass`. A constant ASTRA chose is
   ``DEMO_CONFIG`` and the UI renders a DEMO_CONFIG chip next to it. A constant
   traceable to a published standard is ``REAL_OPEN`` and carries its citation.
   An ASTRA constant is never presented as if it were law (section 4.2).
2. Every constant carries a human-readable ``description``. If a number cannot
   explain itself, it does not ship.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict, Field, model_validator

from astra.domain.enums import HazardType, ProvenanceClass, ServiceType, ZoneClass

MODEL_CONFIG_VERSION = "1.10.0"
"""Bumped whenever any value below changes. Recorded on every audit record."""

ENGINE_VERSION = "0.6.0"
"""Bumped whenever engine logic (not just constants) changes."""

# Citations used repeatedly. Full text in docs/DECISION_MODEL.md (Slice 13).
CITE_SPHERE = (
    "Sphere Association (2018), The Sphere Handbook: Humanitarian Charter and "
    "Minimum Standards in Humanitarian Response, 4th edition."
)
CITE_PMAY_G = (
    "Ministry of Rural Development, Pradhan Mantri Awaas Yojana - Gramin (PMAY-G) "
    "Framework for Implementation, dwelling and plot sizing norms."
)
CITE_GSI_BIS = (
    "Geological Survey of India / BIS IS 14496 (Part 2): Preparation of landslide "
    "hazard zonation maps - weighted factor overlay methodology."
)
CITE_NDMA_SHELTER = (
    "NDMA, Guidelines on Minimum Standards of Relief (relief camp and shelter "
    "provisioning)."
)


class Constant(BaseModel):
    """One inspectable number. Serialised verbatim to ``GET /model/config``."""

    model_config = ConfigDict(frozen=True)

    key: str = Field(description="Stable identifier, referenced by formula specs.")
    value: float = Field(description="The number the engines actually use.")
    unit: str | None = Field(default=None, description="Physical unit, if any.")
    provenance: ProvenanceClass = Field(
        description="DEMO_CONFIG for an ASTRA choice; REAL_OPEN when standard-derived."
    )
    description: str = Field(description="What this number means, in plain language.")
    citation: str | None = Field(
        default=None, description="Published source, when the value is not ASTRA's own."
    )

    def __float__(self) -> float:
        return float(self.value)

    @model_validator(mode="after")
    def _cited_when_not_demo(self) -> Constant:
        if self.provenance is not ProvenanceClass.DEMO_CONFIG and not self.citation:
            raise ValueError(
                f"constant '{self.key}' claims provenance {self.provenance.value} "
                "but carries no citation; either cite it or mark it DEMO_CONFIG"
            )
        return self


def demo(key: str, value: float, description: str, unit: str | None = None) -> Constant:
    """An ASTRA-chosen constant. Rendered with a DEMO_CONFIG chip in the UI."""
    return Constant(
        key=key,
        value=value,
        unit=unit,
        provenance=ProvenanceClass.DEMO_CONFIG,
        description=description,
    )


def cited(
    key: str, value: float, description: str, citation: str, unit: str | None = None
) -> Constant:
    """A constant traceable to a published standard. Rendered with its citation."""
    return Constant(
        key=key,
        value=value,
        unit=unit,
        provenance=ProvenanceClass.REAL_OPEN,
        description=description,
        citation=citation,
    )


class WeightSet(BaseModel):
    """Factor weights for one hazard. Must sum to 1.0 (section 5.1)."""

    model_config = ConfigDict(frozen=True)

    hazard: HazardType
    method_citation: str = CITE_GSI_BIS
    weights: dict[str, Constant]

    @model_validator(mode="after")
    def _sums_to_one(self) -> WeightSet:
        total = sum(c.value for c in self.weights.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"{self.hazard.value} factor weights sum to {total:.6f}, must be 1.0"
            )
        return self

    def w(self, factor: str) -> float:
        return self.weights[factor].value


# ---------------------------------------------------------------------------
# Engine 1 - multi-hazard susceptibility (section 5.1)
# ---------------------------------------------------------------------------


class HazardConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    grid_resolution_m: Constant = demo(
        "hazard.grid_resolution_m",
        100.0,
        "Cell size of the susceptibility grid over the study bounding box.",
        unit="m",
    )
    composite_lambda: Constant = demo(
        "hazard.composite_lambda",
        0.25,
        "Weight on the second-highest hazard in the composite. Preserves dominance "
        "instead of averaging multi-hazard exposure away.",
    )
    zone_threshold_critical: Constant = demo(
        "hazard.zone_threshold.CRITICAL",
        78.0,
        "Composite score at or above which a cell is classified Critical. Set near "
        "the 95th percentile of the corridor's own composite distribution: a class "
        "that covered a third of the terrain would not direct anyone anywhere.",
    )
    zone_threshold_elevated: Constant = demo(
        "hazard.zone_threshold.ELEVATED",
        62.0,
        "Composite score at or above which a cell is classified Elevated. Near the "
        "82nd percentile of the corridor composite distribution.",
    )
    zone_threshold_watch: Constant = demo(
        "hazard.zone_threshold.WATCH",
        52.0,
        "Composite score at or above which a cell is classified Watch. Near the 57th "
        "percentile of the corridor composite distribution.",
    )
    min_mapping_unit_ha: Constant = demo(
        "hazard.min_mapping_unit_ha",
        25.0,
        "Smallest area published as a zone. Clusters below this are removed during "
        "morphological cleaning: at 100 m resolution a single cell above a threshold "
        "is noise, and publishing it as a zone would present noise as a finding.",
        unit="ha",
    )
    zone_buffer_m: Constant = demo(
        "hazard.zone_buffer_m",
        50.0,
        "Outward buffer applied to cleaned zone polygons before exposure intersection.",
        unit="m",
    )
    history_kernel_radius_m: Constant = demo(
        "hazard.history_kernel_radius_m",
        1500.0,
        "Bandwidth of the kernel density estimate over historical incident points.",
        unit="m",
    )

    landslide_weights: WeightSet = WeightSet(
        hazard=HazardType.LANDSLIDE,
        weights={
            "slope": demo("w.landslide.slope", 0.30, "Normalised slope angle."),
            "ruggedness": demo(
                "w.landslide.ruggedness", 0.13, "Terrain ruggedness index from the DEM."
            ),
            "incident_density": demo(
                "w.landslide.incident_density",
                0.21,
                "Kernel density of historical landslide incidents.",
            ),
            "rainfall_intensity": demo(
                "w.landslide.rainfall_intensity",
                0.19,
                "Antecedent rainfall and return-period intensity.",
            ),
            "landcover": demo(
                "w.landslide.landcover",
                0.10,
                "Land-cover and vegetation stability proxy.",
            ),
            "drainage_density": demo(
                "w.landslide.drainage_density",
                0.07,
                "Drainage density as a saturation and undercutting proxy.",
            ),
            # A fault/lineament proximity factor belongs in this model. No open
            # lineament dataset covering the corridor could be obtained, so the
            # factor is absent rather than filled with a neutral constant: a
            # placeholder would look like evidence and would not be one.
        },
    )
    flood_weights: WeightSet = WeightSet(
        hazard=HazardType.FLOOD,
        weights={
            "hand": demo(
                "w.flood.hand", 0.42, "Height above nearest drainage, inverted."
            ),
            "drainage_distance": demo(
                "w.flood.drainage_distance", 0.26, "Distance to the drainage network."
            ),
            "rainfall_intensity": demo(
                "w.flood.rainfall_intensity",
                0.20,
                "Rainfall intensity and return period.",
            ),
            "infiltration": demo(
                "w.flood.infiltration",
                0.12,
                "Infiltration proxy derived from land cover.",
            ),
            # A historical-inundation overlap factor belongs here too. No open
            # inundation extent dataset for this corridor could be obtained, so
            # the factor is absent rather than fabricated.
        },
    )
    cloudburst_weights: WeightSet = WeightSet(
        hazard=HazardType.CLOUDBURST,
        weights={
            "extreme_rainfall_frequency": demo(
                "w.cloudburst.extreme_rainfall_frequency",
                0.30,
                "Frequency of extreme short-duration rainfall events.",
            ),
            "catchment_steepness": demo(
                "w.cloudburst.catchment_steepness",
                0.26,
                "Mean steepness of the contributing catchment.",
            ),
            "confluence_density": demo(
                "w.cloudburst.confluence_density",
                0.20,
                "Density of drainage confluences, where flash flow concentrates.",
            ),
            "upstream_area": demo(
                "w.cloudburst.upstream_area",
                0.24,
                "Upstream contributing area feeding the cell.",
            ),
        },
    )
    coastal_weights: WeightSet = WeightSet(
        hazard=HazardType.COASTAL_EROSION,
        weights={
            "shoreline_retreat_rate": demo(
                "w.coastal.shoreline_retreat_rate",
                0.35,
                "Observed shoreline retreat rate.",
            ),
            "elevation": demo(
                "w.coastal.elevation",
                0.25,
                "Elevation above mean sea level, inverted.",
            ),
            "coastline_distance": demo(
                "w.coastal.coastline_distance", 0.22, "Distance to the coastline."
            ),
            "surge_exposure": demo(
                "w.coastal.surge_exposure", 0.18, "Storm-surge exposure proxy."
            ),
        },
    )

    def weights_for(self, hazard: HazardType) -> WeightSet:
        return {
            HazardType.LANDSLIDE: self.landslide_weights,
            HazardType.FLOOD: self.flood_weights,
            HazardType.CLOUDBURST: self.cloudburst_weights,
            HazardType.COASTAL_EROSION: self.coastal_weights,
        }[hazard]

    def zone_class_for(self, composite: float) -> ZoneClass:
        """Composite score to zone class. One hop, no hidden logic."""
        if composite >= self.zone_threshold_critical.value:
            return ZoneClass.CRITICAL
        if composite >= self.zone_threshold_elevated.value:
            return ZoneClass.ELEVATED
        if composite >= self.zone_threshold_watch.value:
            return ZoneClass.WATCH
        return ZoneClass.LOW


# ---------------------------------------------------------------------------
# Engines 2 and 3 - exposure, vulnerability, priority, phasing
# ---------------------------------------------------------------------------


class NormalisationConfig(BaseModel):
    """Bounds that turn a physical measurement into a 0-1 factor value.

    A weighted overlay is only meaningful if each factor is normalised on a
    stated scale. These are the stated scales. Each pair is a linear ramp: at or
    below ``low`` the factor contributes 0, at or above ``high`` it contributes
    1, and inverted factors (where less is worse) ramp the other way. The bounds
    are ASTRA choices, informed by the observed range in the corridor, and every
    one of them is visible in the transparency panel.
    """

    model_config = ConfigDict(frozen=True)

    slope_low_deg: Constant = demo(
        "norm.slope_low_deg",
        10.0,
        "Slope at or below which slope contributes nothing to landslide susceptibility.",
        unit="degrees",
    )
    slope_high_deg: Constant = demo(
        "norm.slope_high_deg",
        45.0,
        "Slope at or above which the slope factor is saturated. Above roughly this "
        "angle, loose material has already shed and failure behaviour changes.",
        unit="degrees",
    )
    ruggedness_low_m: Constant = demo(
        "norm.ruggedness_low_m", 2.0, "Terrain ruggedness contributing nothing.", unit="m"
    )
    ruggedness_high_m: Constant = demo(
        "norm.ruggedness_high_m", 45.0, "Terrain ruggedness at full contribution.", unit="m"
    )
    drainage_density_low: Constant = demo(
        "norm.drainage_density_low",
        0.0,
        "Drainage density contributing nothing.",
        unit="km/km2",
    )
    drainage_density_high: Constant = demo(
        "norm.drainage_density_high",
        12.0,
        "Drainage density at full contribution.",
        unit="km/km2",
    )
    hand_low_m: Constant = demo(
        "norm.hand_low_m",
        3.0,
        "Height above nearest drainage at or below which flood exposure is saturated.",
        unit="m",
    )
    hand_high_m: Constant = demo(
        "norm.hand_high_m",
        45.0,
        "Height above nearest drainage at or above which riverine flood exposure is "
        "treated as negligible.",
        unit="m",
    )
    drainage_distance_low_m: Constant = demo(
        "norm.drainage_distance_low_m",
        25.0,
        "Distance to a channel at or below which proximity is saturated.",
        unit="m",
    )
    drainage_distance_high_m: Constant = demo(
        "norm.drainage_distance_high_m",
        600.0,
        "Distance to a channel beyond which proximity contributes nothing.",
        unit="m",
    )
    rainfall_intensity_low_mm: Constant = demo(
        "norm.rainfall_intensity_low_mm",
        50.0,
        "Annual maximum daily rainfall at or below which the intensity factor is zero.",
        unit="mm/day",
    )
    rainfall_intensity_high_mm: Constant = demo(
        "norm.rainfall_intensity_high_mm",
        120.0,
        "Annual maximum daily rainfall at or above which the intensity factor saturates.",
        unit="mm/day",
    )
    extreme_rain_threshold_mm: Constant = demo(
        "norm.extreme_rain_threshold_mm",
        64.5,
        "Daily rainfall counted as a heavy-rainfall day. Matches the IMD 'heavy "
        "rainfall' class boundary of 64.5 mm in 24 hours.",
        unit="mm/day",
    )
    extreme_rain_days_low: Constant = demo(
        "norm.extreme_rain_days_low",
        0.3,
        "Heavy-rainfall days per year at or below which the cloudburst frequency "
        "factor is zero.",
        unit="days/year",
    )
    extreme_rain_days_high: Constant = demo(
        "norm.extreme_rain_days_high",
        3.0,
        "Heavy-rainfall days per year at which the frequency factor saturates.",
        unit="days/year",
    )
    catchment_slope_low_deg: Constant = demo(
        "norm.catchment_slope_low_deg",
        10.0,
        "Mean catchment slope contributing nothing to flash-flood susceptibility.",
        unit="degrees",
    )
    catchment_slope_high_deg: Constant = demo(
        "norm.catchment_slope_high_deg",
        45.0,
        "Mean catchment slope at full contribution.",
        unit="degrees",
    )
    upstream_area_low_km2: Constant = demo(
        "norm.upstream_area_low_km2",
        0.5,
        "Upstream contributing area at or below which flow concentration is negligible. "
        "Normalised logarithmically between the bounds, since discharge scales with "
        "area over orders of magnitude.",
        unit="km2",
    )
    upstream_area_high_km2: Constant = demo(
        "norm.upstream_area_high_km2",
        200.0,
        "Upstream contributing area at which the concentration factor saturates.",
        unit="km2",
    )
    density_percentile: Constant = demo(
        "norm.density_percentile",
        95.0,
        "Percentile of a kernel-density surface used as its normalisation ceiling, so "
        "one exceptional cluster cannot flatten the rest of the map.",
    )
    coastal_retreat_low_m_yr: Constant = demo(
        "norm.coastal_retreat_low_m_yr", 0.0, "Shoreline retreat contributing nothing.",
        unit="m/year",
    )
    coastal_retreat_high_m_yr: Constant = demo(
        "norm.coastal_retreat_high_m_yr", 5.0, "Shoreline retreat at full contribution.",
        unit="m/year",
    )
    coastal_elevation_low_m: Constant = demo(
        "norm.coastal_elevation_low_m",
        0.0,
        "Elevation at or below which coastal exposure is saturated.",
        unit="m",
    )
    coastal_elevation_high_m: Constant = demo(
        "norm.coastal_elevation_high_m",
        15.0,
        "Elevation above which coastal erosion exposure is treated as negligible.",
        unit="m",
    )
    coastline_distance_low_m: Constant = demo(
        "norm.coastline_distance_low_m", 0.0, "Distance to the coastline, saturated.",
        unit="m",
    )
    coastline_distance_high_m: Constant = demo(
        "norm.coastline_distance_high_m",
        2000.0,
        "Distance to the coastline beyond which erosion exposure is negligible.",
        unit="m",
    )


class EvidenceConfig(BaseModel):
    """How recorded evidence is weighted and interpolated before it is scored.

    The incident inventory is a record of reported events, so it carries a
    reporting bias towards roads, settlements and media attention. These
    constants decide how much weight each record carries; they do not correct
    that bias, and nothing here claims to.
    """

    model_config = ConfigDict(frozen=True)

    incident_weight_small: Constant = demo(
        "evidence.incident_weight.small", 0.5, "Relative weight of a small recorded event."
    )
    incident_weight_medium: Constant = demo(
        "evidence.incident_weight.medium", 1.0, "Relative weight of a medium event."
    )
    incident_weight_large: Constant = demo(
        "evidence.incident_weight.large", 2.0, "Relative weight of a large event."
    )
    incident_weight_very_large: Constant = demo(
        "evidence.incident_weight.very_large",
        3.0,
        "Relative weight of a very large event.",
    )
    incident_weight_unknown: Constant = demo(
        "evidence.incident_weight.unknown",
        1.0,
        "Relative weight where the record does not state a size.",
    )
    incident_fatality_bonus: Constant = demo(
        "evidence.incident_fatality_bonus",
        0.05,
        "Additional weight per recorded fatality, capped, so that events with a "
        "documented human toll count for more than an unattributed report.",
    )
    incident_fatality_bonus_cap: Constant = demo(
        "evidence.incident_fatality_bonus_cap",
        2.0,
        "Ceiling on the fatality contribution to a single event's weight.",
    )
    incident_max_location_error_km: Constant = demo(
        "evidence.incident_max_location_error_km",
        25.0,
        "Records whose stated location accuracy is coarser than this are excluded: "
        "at 100 m grid resolution they would smear evidence across whole valleys.",
        unit="km",
    )
    rainfall_idw_power: Constant = demo(
        "evidence.rainfall_idw_power",
        2.0,
        "Inverse-distance weighting exponent used to interpolate the rainfall grid "
        "points across the corridor.",
    )


class PriorityConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    w_hazard: Constant = demo(
        "priority.w_hazard",
        0.35,
        "Weight on composite hazard sampled over the habitation footprint.",
    )
    w_exposure: Constant = demo(
        "priority.w_exposure",
        0.25,
        "Weight on population and critical-facility exposure.",
    )
    w_vulnerability: Constant = demo(
        "priority.w_vulnerability",
        0.25,
        "Weight on the demographic vulnerability index.",
    )
    w_history: Constant = demo(
        "priority.w_history", 0.15, "Weight on recency-weighted incident history."
    )

    exposure_w_population: Constant = demo(
        "exposure.w_population",
        0.55,
        "Share of exposure driven by resident population.",
    )
    exposure_w_households: Constant = demo(
        "exposure.w_households",
        0.20,
        "Share of exposure driven by household count.",
    )
    exposure_w_facilities: Constant = demo(
        "exposure.w_facilities",
        0.25,
        "Share of exposure driven by critical facilities (school, clinic, anganwadi).",
    )

    vuln_w_elderly: Constant = demo(
        "vulnerability.w_elderly", 0.20, "Share of residents aged 60 and above."
    )
    vuln_w_children_u5: Constant = demo(
        "vulnerability.w_children_u5", 0.18, "Share of children under five."
    )
    vuln_w_disability: Constant = demo(
        "vulnerability.w_disability", 0.18, "Share of persons with disabilities."
    )
    vuln_w_medical_dependency: Constant = demo(
        "vulnerability.w_medical_dependency",
        0.14,
        "Share of medically dependent residents.",
    )
    vuln_w_low_income: Constant = demo(
        "vulnerability.w_low_income",
        0.15,
        "Share of single-earner or low-income households.",
    )
    vuln_w_kutcha_share: Constant = demo(
        "vulnerability.w_kutcha_share",
        0.15,
        "Structural typology proxy: kutcha and semi-pucca dwelling share.",
    )

    footprint_radius_m: Constant = demo(
        "priority.footprint_radius_m",
        300.0,
        "Radius over which composite hazard is summarised for a habitation. These "
        "settlements are compact; 300 m covers the built footprint and the slope "
        "immediately above it without reaching into the next valley.",
        unit="m",
    )
    footprint_w_mean: Constant = demo(
        "priority.footprint_w_mean",
        0.60,
        "Weight on the mean composite across the habitation footprint. The mean "
        "describes the ground the settlement actually sits on.",
    )
    footprint_w_max: Constant = demo(
        "priority.footprint_w_max",
        0.40,
        "Weight on the maximum composite within the footprint. The maximum is what "
        "can reach the settlement, so it counts - but taking the maximum alone would "
        "push every settlement in this terrain to 100 and destroy the ranking.",
    )
    exposure_reference_population: Constant = demo(
        "exposure.reference_population",
        1500.0,
        "Population at which the population component of exposure saturates. A fixed "
        "reference rather than the largest settlement in the set, so a score does not "
        "change when a habitation is added or removed.",
        unit="persons",
    )
    exposure_reference_households: Constant = demo(
        "exposure.reference_households",
        300.0,
        "Household count at which the household component of exposure saturates.",
        unit="households",
    )
    exposure_reference_facilities: Constant = demo(
        "exposure.reference_facilities",
        4.0,
        "Number of critical facilities at which that component of exposure saturates.",
        unit="facilities",
    )
    history_reference_weighted_events: Constant = demo(
        "history.reference_weighted_events",
        1.5,
        "Recency-weighted incident total at which the history factor saturates. Fixed "
        "so the factor means the same thing in every scenario. Set against the "
        "observed record for this corridor: reported inventories are sparse, and a "
        "reference set far above what the record can produce would make the one real "
        "evidence layer contribute nothing.",
    )

    # --- The reference profile that puts vulnerability on the same 0-1 scale as
    # --- hazard. Without it, a weighted average of small demographic shares
    # --- lands near 0.2 for every settlement and the vulnerability weight does
    # --- nothing, whatever the configuration says it should do.
    vuln_reference_elderly: Constant = demo(
        "vulnerability.reference.elderly",
        0.25,
        "Elderly share in the acutely vulnerable reference profile against which the "
        "vulnerability index is scaled.",
    )
    vuln_reference_children_u5: Constant = demo(
        "vulnerability.reference.children_u5",
        0.15,
        "Under-five share in the acutely vulnerable reference profile.",
    )
    vuln_reference_disability: Constant = demo(
        "vulnerability.reference.disability",
        0.06,
        "Disability share in the acutely vulnerable reference profile.",
    )
    vuln_reference_medical_dependency: Constant = demo(
        "vulnerability.reference.medical_dependency",
        0.05,
        "Medically dependent share in the acutely vulnerable reference profile.",
    )
    vuln_reference_low_income: Constant = demo(
        "vulnerability.reference.low_income",
        0.70,
        "Low-income household share in the acutely vulnerable reference profile.",
    )
    vuln_reference_kutcha: Constant = demo(
        "vulnerability.reference.kutcha_semi_pucca",
        1.00,
        "Kutcha and semi-pucca dwelling share in the acutely vulnerable reference "
        "profile: every dwelling of weak construction.",
    )
    history_tau_days: Constant = demo(
        "history.tau_days",
        3650.0,
        "Exponential decay constant for incident recency, ten years. A slope that "
        "failed a decade ago is still a slope that fails; a five-year memory would "
        "discard most of the published record for this corridor.",
        unit="days",
    )
    history_radius_m: Constant = demo(
        "history.radius_m",
        3000.0,
        "Radius around a habitation within which historical incidents are counted.",
        unit="m",
    )

    tier_immediate_min_priority: Constant = demo(
        "tier.immediate.min_priority",
        55.0,
        "Priority score at or above which a habitation is considered for Immediate. "
        "A policy choice about how much a district can act on at once, not a "
        "statutory trigger: set so the Immediate tier covers the settlements a "
        "district could plausibly move within one season.",
    )
    tier_short_term_min_priority: Constant = demo(
        "tier.short_term.min_priority",
        45.0,
        "Priority score at or above which a habitation is considered for Short-term "
        "relocation, once bottlenecks identified by the capacity engine are relieved.",
    )
    tier_medium_term_min_priority: Constant = demo(
        "tier.medium_term.min_priority",
        35.0,
        "Priority score at or above which a habitation enters Medium-term planning.",
    )
    override_critical_vulnerability: Constant = demo(
        "tier.override.critical_zone_vulnerability",
        0.55,
        "Vulnerability index at or above which a habitation inside a Critical zone is "
        "escalated to Immediate regardless of its composite priority score.",
    )
    override_critical_zone_population: Constant = demo(
        "tier.override.critical_zone_population",
        400.0,
        "Population inside a Critical zone at or above which a habitation is escalated "
        "to Immediate regardless of its composite priority score. A large settlement "
        "on Critical ground is an immediate question even when its averaged score is "
        "not the highest in the district.",
        unit="persons",
    )
    confidence_synthetic_penalty: Constant = demo(
        "confidence.synthetic_input_penalty",
        0.45,
        "Provenance score assigned to a synthetic input when computing a habitation's "
        "evidence confidence. Demographic composition is assumed, so confidence in a "
        "habitation's priority is lower than confidence in the terrain beneath it.",
    )

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> PriorityConfig:
        groups = (
            (
                "priority",
                (self.w_hazard, self.w_exposure, self.w_vulnerability, self.w_history),
            ),
            (
                "exposure",
                (
                    self.exposure_w_population,
                    self.exposure_w_households,
                    self.exposure_w_facilities,
                ),
            ),
            (
                "vulnerability",
                (
                    self.vuln_w_elderly,
                    self.vuln_w_children_u5,
                    self.vuln_w_disability,
                    self.vuln_w_medical_dependency,
                    self.vuln_w_low_income,
                    self.vuln_w_kutcha_share,
                ),
            ),
        )
        for label, parts in groups:
            total = sum(c.value for c in parts)
            if abs(total - 1.0) > 1e-9:
                raise ValueError(f"{label} weights sum to {total:.6f}, must be 1.0")
        return self


# ---------------------------------------------------------------------------
# Engine 4 - suitability gates and carrying capacity
# ---------------------------------------------------------------------------


class CapacityConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # --- Published standards. Real, cited, rendered with their source. ---
    water_litres_per_person_day: Constant = cited(
        "capacity.water_lpcd",
        15.0,
        "Minimum water supply per person per day.",
        CITE_SPHERE,
        unit="L/person/day",
    )
    persons_per_latrine: Constant = cited(
        "capacity.persons_per_latrine",
        20.0,
        "Maximum number of persons sharing one latrine.",
        CITE_SPHERE,
        unit="persons/latrine",
    )
    covered_area_m2_per_person: Constant = cited(
        "capacity.covered_area_m2_per_person",
        3.5,
        "Minimum covered living area per person.",
        CITE_SPHERE,
        unit="m2/person",
    )
    site_area_m2_per_person: Constant = cited(
        "capacity.site_area_m2_per_person",
        45.0,
        "Minimum total site area per person including services and circulation.",
        CITE_SPHERE,
        unit="m2/person",
    )
    persons_per_health_facility: Constant = cited(
        "capacity.persons_per_health_facility",
        10000.0,
        "Population served by one health facility unit at minimum standard.",
        CITE_SPHERE,
        unit="persons/facility",
    )
    pmay_g_plot_area_m2: Constant = cited(
        "capacity.pmay_g_plot_area_m2",
        25.0,
        "Minimum dwelling unit area for permanent rural resettlement.",
        CITE_PMAY_G,
        unit="m2/dwelling",
    )
    shelter_occupancy_persons_per_unit: Constant = cited(
        "capacity.shelter_occupancy",
        5.0,
        "Design occupancy of one shelter unit, matched to average rural household size.",
        CITE_NDMA_SHELTER,
        unit="persons/unit",
    )

    # --- ASTRA choices. Marked DEMO_CONFIG, never presented as a rule. ---
    power_kva_per_household: Constant = demo(
        "capacity.power_kva_per_household",
        1.0,
        "Assumed connected load per household for the power capacity constraint.",
        unit="kVA/household",
    )
    persons_per_household: Constant = demo(
        "capacity.persons_per_household",
        5.0,
        "Household size used to convert person counts to household counts.",
        unit="persons/household",
    )
    gate_max_slope_deg: Constant = demo(
        "gate.max_slope_deg",
        18.0,
        "Slope above which a candidate site fails the build-safe gate.",
        unit="degrees",
    )
    gate_hazard_buffer_m: Constant = demo(
        "gate.hazard_buffer_m",
        250.0,
        "Safety buffer around Critical and Elevated zones a site must sit outside of.",
        unit="m",
    )
    gate_flood_return_period_years: Constant = demo(
        "gate.flood_return_period_years",
        100.0,
        "Return-period flood level a candidate site must sit above.",
        unit="years",
    )
    gate_max_road_distance_m: Constant = demo(
        "gate.max_road_distance_m",
        1000.0,
        "Distance to the nearest usable road beyond which a site is not accessible.",
        unit="m",
    )
    gate_min_hand_m: Constant = demo(
        "gate.min_hand_m",
        20.0,
        "Height above nearest drainage a candidate site must stand at to pass the "
        "flood gate. ASTRA does not run a hydraulic model, so HAND is used as the "
        "return-period stand-in and is labelled as one: it says how far above its "
        "channel a site sits, not what a 100-year discharge would do.",
        unit="m",
    )
    site_measure_radius_m: Constant = demo(
        "capacity.site_measure_radius_m",
        650.0,
        "Radius around a candidate site centre within which usable area is measured "
        "on the real land-cover, slope and drainage surfaces.",
        unit="m",
    )
    intervention_water_litres_day: Constant = demo(
        "intervention.water_litres_per_day",
        15000.0,
        "Water supply added by one intervention unit: a borewell with storage, sized "
        "at roughly one thousand people at the Sphere minimum.",
        unit="L/day",
    )
    intervention_sanitation_units: Constant = demo(
        "intervention.sanitation_units",
        20.0,
        "Latrine units added by one sanitation intervention block.",
        unit="latrines",
    )
    intervention_shelter_units: Constant = demo(
        "intervention.shelter_units",
        40.0,
        "Shelter units added by one construction intervention.",
        unit="units",
    )
    intervention_healthcare_units: Constant = demo(
        "intervention.healthcare_units",
        0.25,
        "Health facility capacity added by one intervention: a sub-centre serving a "
        "quarter of the population one full facility unit covers.",
        unit="facility units",
    )
    intervention_power_kva: Constant = demo(
        "intervention.power_kva",
        150.0,
        "Connected load added by one power intervention: a distribution transformer.",
        unit="kVA",
    )
    intervention_land_m2: Constant = demo(
        "intervention.land_m2",
        10000.0,
        "Usable land added by one land intervention: one hectare of terracing or "
        "acquisition adjacent to the site.",
        unit="m2",
    )
    access_persons_per_route_day: Constant = demo(
        "capacity.access_persons_per_route_day",
        3200.0,
        "People one usable approach route can deliver to a site, being the route "
        "throughput ceiling times the daily movement window. Access capacity is the "
        "number of approach routes times this figure - the ACCESS row on the capacity "
        "table, supplied by the route engine.",
        unit="persons per route",
    )
    landcover_agreement_high_confidence: Constant = demo(
        "capacity.landcover_agreement_high",
        0.85,
        "Agreement between the WorldCover reclassification and the Random Forest "
        "refinement above which usable-area confidence is reported as High.",
    )

    def norm_for(self, service: ServiceType) -> Constant | None:
        """Per-service demand norm. ``None`` where capacity is supplied directly."""
        return {
            ServiceType.LAND: self.site_area_m2_per_person,
            ServiceType.SHELTER: self.shelter_occupancy_persons_per_unit,
            ServiceType.WATER: self.water_litres_per_person_day,
            ServiceType.SANITATION: self.persons_per_latrine,
            ServiceType.HEALTHCARE: self.persons_per_health_facility,
            ServiceType.POWER: self.power_kva_per_household,
            ServiceType.ACCESS: None,
        }[service]


# ---------------------------------------------------------------------------
# Engine 5 - route reliability and survivability
# ---------------------------------------------------------------------------


class RouteConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    speed_national_highway_kmh: Constant = demo(
        "route.speed.national_highway_kmh",
        50.0,
        "Free-flow speed assumed on a national highway segment.",
        unit="km/h",
    )
    speed_state_highway_kmh: Constant = demo(
        "route.speed.state_highway_kmh",
        40.0,
        "Free-flow speed assumed on a state highway segment.",
        unit="km/h",
    )
    speed_district_road_kmh: Constant = demo(
        "route.speed.district_road_kmh",
        30.0,
        "Free-flow speed assumed on a district road segment.",
        unit="km/h",
    )
    speed_village_road_kmh: Constant = demo(
        "route.speed.village_road_kmh",
        20.0,
        "Free-flow speed assumed on a village road segment.",
        unit="km/h",
    )
    speed_track_kmh: Constant = demo(
        "route.speed.track_kmh",
        10.0,
        "Free-flow speed assumed on an unsurfaced track.",
        unit="km/h",
    )

    p_fail_hazard_coefficient: Constant = demo(
        "route.p_fail.hazard_coefficient",
        0.015,
        "Probability that one reference length of road at maximum modelled hazard "
        "susceptibility is impassable when relocation movement is required. This is a "
        "planning-horizon figure, not a per-journey one: it asks whether a road is "
        "usable across the weeks a phased relocation runs, allowing for normal "
        "clearance and restoration, which is why it is far below the chance of a road "
        "being blocked at some point during a monsoon.",
    )
    p_fail_reference_length_m: Constant = demo(
        "route.p_fail.reference_length_m",
        1000.0,
        "Length of fully hazard-exposed road over which the failure coefficient "
        "applies in full. Hazard-driven failure is treated as independent per unit of "
        "exposed length, so a stretch carrying twice this exposure carries the "
        "compounded chance of failing somewhere along it. This is what makes a route's "
        "reliability a property of the road rather than of how many junctions OSM "
        "happens to have mapped along it.",
        unit="m",
    )
    p_fail_bridge_dependency: Constant = demo(
        "route.p_fail.bridge_dependency",
        0.01,
        "Additional failure probability for a segment carrying a bridge or culvert. "
        "Not scaled by length: a structure does not fail gradually. Kept low because "
        "the OpenStreetMap bridge tag does not distinguish a major span over the "
        "Alaknanda from a metre-wide culvert, and ASTRA does not have the structural "
        "inventory that would let it tell them apart.",
    )
    safest_risk_alpha: Constant = demo(
        "route.safest_alpha",
        2.0,
        "Risk aversion in the SAFEST objective: minimise time x (1 + alpha x risk).",
    )
    min_reliability_threshold: Constant = demo(
        "route.min_reliability",
        0.60,
        "Route reliability below which a site is treated as infeasible for a "
        "habitation in the optimiser, not merely penalised. A road ASTRA would not "
        "plan a relocation convoy down is not a slightly worse road. This threshold "
        "is a policy choice about acceptable risk, not a physical constant, and the "
        "weight sensitivity analysis is required to test what moves when it moves.",
    )
    throughput_persons_per_hour: Constant = demo(
        "route.throughput_persons_per_hour",
        400.0,
        "Movement throughput ceiling of a single usable route, feeding the ACCESS "
        "capacity constraint.",
        unit="persons/hour",
    )
    access_movement_window_hours: Constant = demo(
        "route.access_window_hours",
        8.0,
        "Hours of usable movement in a relocation day - daylight on mountain roads, "
        "less the time convoys are not running. Throughput times this window is how "
        "many people the approach to a site can deliver, which is what makes ACCESS a "
        "capacity constraint rather than a yes/no.",
        unit="h",
    )
    hazard_segment_exposure_threshold: Constant = demo(
        "route.hazard_segment_threshold",
        0.60,
        "Normalised composite hazard at or above which a road segment counts as "
        "hazard-exposed for the route's exposure tally and its longest continuous run.",
    )
    point_of_failure_p_fail: Constant = demo(
        "route.point_of_failure_p_fail",
        0.04,
        "Segment failure probability at or above which a stretch of road is listed as "
        "a point of failure on a route. Every segment of a single-path route is "
        "technically one; this threshold, with the bridge test and the "
        "no-way-around test, picks out the ones worth an SDMA's attention. It sits "
        "near the 95th percentile of segment failure probability in this corridor, so "
        "the list names the worst of the road rather than all of it.",
    )


# ---------------------------------------------------------------------------
# Engine 6 - constrained relocation optimisation
# ---------------------------------------------------------------------------


class OptimiserConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    beta_unmet_demand: Constant = demo(
        "opt.beta1_unmet_demand",
        1000.0,
        "Penalty per unrelocated person, scaled by that habitation's priority.",
    )
    beta_travel_time: Constant = demo(
        "opt.beta2_travel_time", 1.0, "Penalty per person-minute of travel."
    )
    beta_route_risk: Constant = demo(
        "opt.beta3_route_risk",
        250.0,
        "Penalty per unit of person-weighted route risk.",
    )
    beta_site_overload: Constant = demo(
        "opt.beta4_site_overload",
        60.0,
        "Penalty per person placed above a site's soft capacity share. A site run "
        "to its ceiling has no margin for the household that arrives late or the "
        "service that underperforms, so the optimiser should prefer not to - but "
        "only prefer. This value is deliberately well below the per-person cost of "
        "leaving someone in a red zone, because a plan that strands people to "
        "protect a 15% margin is not a plan an SDMA can defend.",
    )
    beta_livelihood_disruption: Constant = demo(
        "opt.beta5_livelihood_disruption",
        300.0,
        "Penalty per unit of computed livelihood disruption.",
    )
    beta_fragmentation: Constant = demo(
        "opt.beta6_fragmentation",
        150.0,
        "Penalty for splitting one habitation across multiple destination sites. It "
        "charges splits between places, not between phases: a village moved to one "
        "site over two phases is a phased relocation, not a divided community.",
    )
    beta_phase_delay: Constant = demo(
        "opt.beta7_phase_delay",
        25.0,
        "Penalty per person per phase of delay, scaled by that habitation's priority. "
        "Without it, moving someone in the medium term and moving them now cost the "
        "same, and 'Immediate' would be a label on a ranking rather than a claim "
        "about when people leave. It is deliberately small: its job is to order the "
        "plan, not to change who is in it. At four times this value the solver began "
        "declining late moves altogether and placed a hundred fewer people, which is "
        "the wrong trade and is asserted against in the tests.",
    )
    solver_time_limit_s: Constant = demo(
        "opt.time_limit_s",
        10.0,
        "CP-SAT wall-clock limit. On expiry the deterministic greedy fallback runs "
        "and the response is labelled FALLBACK.",
        unit="s",
    )
    solver_seed: Constant = demo(
        "opt.random_seed",
        20260191.0,
        "Fixed solver seed so every demo run is reproducible.",
    )
    min_assignment_block: Constant = demo(
        "opt.min_assignment_block",
        25.0,
        "Household-integrity floor: no assignment smaller than this many people, to "
        "avoid absurd fragmentation of a settlement.",
        unit="persons",
    )
    max_travel_minutes_immediate: Constant = demo(
        "opt.max_travel_minutes.IMMEDIATE",
        60.0,
        "Travel-time ceiling for an Immediate-phase assignment.",
        unit="min",
    )
    max_travel_minutes_short_term: Constant = demo(
        "opt.max_travel_minutes.SHORT_TERM",
        120.0,
        "Travel-time ceiling for a Short-term-phase assignment.",
        unit="min",
    )
    max_travel_minutes_medium_term: Constant = demo(
        "opt.max_travel_minutes.MEDIUM_TERM",
        180.0,
        "Travel-time ceiling for a Medium-term-phase assignment.",
        unit="min",
    )
    site_soft_capacity_share: Constant = demo(
        "opt.site_soft_capacity_share",
        0.85,
        "Share of a site's effective capacity beyond which each further person "
        "incurs the overload penalty. A site run to its ceiling has no margin for "
        "the household that arrives late or the service that underperforms, so the "
        "optimiser has to be given a reason to accept that - not forbidden from it.",
    )
    phase_capacity_share_immediate: Constant = demo(
        "opt.phase_capacity_share.IMMEDIATE",
        0.45,
        "Share of a site's effective capacity that can be occupied in the Immediate "
        "phase. Sites are not built out on day one: water, sanitation and shelter "
        "arrive over weeks, and the ramp says how much of the site is standing when "
        "the first movement happens.",
    )
    phase_capacity_share_short_term: Constant = demo(
        "opt.phase_capacity_share.SHORT_TERM",
        0.75,
        "Cumulative share of a site's effective capacity occupied by the end of the "
        "Short-term phase.",
    )
    phase_capacity_share_medium_term: Constant = demo(
        "opt.phase_capacity_share.MEDIUM_TERM",
        1.0,
        "Cumulative share by the end of the Medium-term phase: the whole assessed "
        "effective capacity.",
    )
    livelihood_commute_ceiling_min: Constant = demo(
        "livelihood.commute_ceiling_min",
        90.0,
        "Routed travel time from a destination site back to the origin livelihood "
        "centre at which commute disruption is scored at maximum.",
        unit="min",
    )
    livelihood_market_ceiling_min: Constant = demo(
        "livelihood.market_ceiling_min",
        60.0,
        "Routed travel time from a site to the nearest trunk road at which market "
        "and service access is scored at maximum disruption. Trunk roads are the "
        "measurable proxy ASTRA has for where a district's markets, banks and "
        "offices are; it is not a survey of them.",
        unit="min",
    )
    livelihood_w_travel_time: Constant = demo(
        "livelihood.w_travel_time",
        0.40,
        "Share of livelihood disruption from travel time back to the origin "
        "livelihood centre. Livelihood disruption is computed, never a fixed km rule.",
    )
    livelihood_w_connectivity: Constant = demo(
        "livelihood.w_connectivity",
        0.20,
        "Share from the connectivity class of the destination road link.",
    )
    livelihood_w_road_reliability: Constant = demo(
        "livelihood.w_road_reliability",
        0.20,
        "Share from year-round reliability of that link.",
    )
    livelihood_w_market_access: Constant = demo(
        "livelihood.w_market_access",
        0.20,
        "Share from market and service access at the destination.",
    )

    @model_validator(mode="after")
    def _livelihood_weights_sum_to_one(self) -> OptimiserConfig:
        total = sum(
            c.value
            for c in (
                self.livelihood_w_travel_time,
                self.livelihood_w_connectivity,
                self.livelihood_w_road_reliability,
                self.livelihood_w_market_access,
            )
        )
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"livelihood weights sum to {total:.6f}, must be 1.0")
        return self


# ---------------------------------------------------------------------------
# Confidence and validation
# ---------------------------------------------------------------------------


class ConfidenceConfig(BaseModel):
    """Confidence is computed separately and never multiplied into priority."""

    model_config = ConfigDict(frozen=True)

    w_data_completeness: Constant = demo(
        "confidence.w_data_completeness",
        0.30,
        "Share of confidence from how many required inputs were present.",
    )
    w_provenance_mix: Constant = demo(
        "confidence.w_provenance_mix",
        0.30,
        "Share from the provenance mix of the inputs: real observed data raises "
        "confidence, synthetic and demo constants lower it.",
    )
    w_evidence_recency: Constant = demo(
        "confidence.w_evidence_recency",
        0.25,
        "Share from how recent the supporting evidence is.",
    )
    w_spatial_resolution: Constant = demo(
        "confidence.w_spatial_resolution",
        0.15,
        "Share from the spatial resolution of the coarsest contributing layer.",
    )
    band_high_min: Constant = demo(
        "confidence.band.HIGH_min",
        0.70,
        "Confidence at or above which the reported band is High.",
    )
    band_medium_min: Constant = demo(
        "confidence.band.MEDIUM_min",
        0.45,
        "Confidence at or above which the reported band is Medium.",
    )

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> ConfidenceConfig:
        total = (
            self.w_data_completeness.value
            + self.w_provenance_mix.value
            + self.w_evidence_recency.value
            + self.w_spatial_resolution.value
        )
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"confidence weights sum to {total:.6f}, must be 1.0")
        return self


class GenerationConfig(BaseModel):
    """Assumptions behind the synthetic habitation and site records.

    These are ASTRA demonstration assumptions for a Himalayan hill district, not
    measured statistics. They are exposed here, with a DEMO_CONFIG chip on every
    value, precisely so that "where did your 12 percent elderly come from?" has a
    visible answer rather than a defensive one. Habitation placement and size are
    calibrated to real terrain, land cover, road access and the WorldPop
    population surface; the demographic composition below is assumed.
    """

    model_config = ConfigDict(frozen=True)

    seed: Constant = demo(
        "generation.seed",
        26191.0,
        "Random seed for the synthetic dataset, so the demonstration scenario is "
        "identical on every machine and every run.",
    )
    habitation_count: Constant = demo(
        "generation.habitation_count", 12.0, "Number of synthetic habitations generated."
    )
    site_count: Constant = demo(
        "generation.site_count", 6.0, "Number of synthetic candidate relocation sites."
    )
    min_separation_m: Constant = demo(
        "generation.min_separation_m",
        1500.0,
        "Minimum spacing between generated settlements, so they read as distinct "
        "habitations rather than one cluster.",
        unit="m",
    )
    settlement_catchment_km2: Constant = demo(
        "generation.settlement_catchment_km2",
        0.65,
        "Area over which the WorldPop population surface is integrated to size a "
        "settlement.",
        unit="km2",
    )
    population_min: Constant = demo(
        "generation.population_min", 140.0, "Floor on generated habitation population.",
        unit="persons",
    )
    population_max: Constant = demo(
        "generation.population_max", 1450.0, "Ceiling on generated habitation population.",
        unit="persons",
    )
    settlement_min_elevation_m: Constant = demo(
        "generation.settlement_min_elevation_m",
        900.0,
        "Lower elevation bound for plausible year-round habitation in this corridor.",
        unit="m",
    )
    settlement_max_elevation_m: Constant = demo(
        "generation.settlement_max_elevation_m",
        2600.0,
        "Upper elevation bound for plausible year-round habitation in this corridor.",
        unit="m",
    )
    settlement_max_road_distance_m: Constant = demo(
        "generation.settlement_max_road_distance_m",
        900.0,
        "Habitations are placed within this distance of a mapped road, matching the "
        "observed pattern of road-linked hill settlement.",
        unit="m",
    )
    share_elderly: Constant = demo(
        "generation.share_elderly",
        0.135,
        "Assumed share of residents aged 60 and above. Set above a national rural "
        "average to reflect working-age out-migration from hill districts.",
    )
    share_children_u5: Constant = demo(
        "generation.share_children_u5",
        0.082,
        "Assumed share of children under five.",
    )
    share_disability: Constant = demo(
        "generation.share_disability",
        0.028,
        "Assumed share of persons with disabilities.",
    )
    share_medical_dependency: Constant = demo(
        "generation.share_medical_dependency",
        0.021,
        "Assumed share of residents dependent on regular medical support.",
    )
    share_low_income_households: Constant = demo(
        "generation.share_low_income_households",
        0.38,
        "Assumed share of households that are single-earner or low-income.",
    )
    demographic_jitter: Constant = demo(
        "generation.demographic_jitter",
        0.25,
        "Fractional spread applied to each assumed share across habitations, so the "
        "dataset is not twelve identical villages.",
    )
    kutcha_share_remote: Constant = demo(
        "generation.kutcha_share_remote",
        0.42,
        "Assumed kutcha dwelling share for the least road-accessible habitations.",
    )
    kutcha_share_connected: Constant = demo(
        "generation.kutcha_share_connected",
        0.16,
        "Assumed kutcha dwelling share for the best-connected habitations.",
    )


class ValidationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    sensitivity_runs: Constant = demo(
        "validation.sensitivity_runs",
        1000.0,
        "Monte Carlo runs used for weight-sensitivity and rank-stability analysis.",
    )
    sensitivity_perturbation: Constant = demo(
        "validation.sensitivity_perturbation",
        0.20,
        "Fractional perturbation applied to every weight in each Monte Carlo run.",
    )
    backtest_background_points: Constant = demo(
        "validation.backtest_background_points",
        2000.0,
        "Sampled non-incident background points used as negatives in the ROC-AUC "
        "back-test of the composite susceptibility surface.",
    )
    rank_stability_top_k: Constant = demo(
        "validation.rank_stability_top_k",
        5.0,
        "Size of the top-k set whose stability under perturbation is reported.",
    )


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------


class AstraModelConfig(BaseModel):
    """Root of the versioned config tree, served verbatim by ``GET /model/config``."""

    model_config = ConfigDict(frozen=True)

    version: str = MODEL_CONFIG_VERSION
    engine_version: str = ENGINE_VERSION
    disclaimer: str = (
        "Values marked DEMO_CONFIG are ASTRA's own analytical choices, not government "
        "rules or statutory thresholds. Values marked REAL_OPEN carry the published "
        "standard they are drawn from."
    )
    hazard: HazardConfig = HazardConfig()
    normalisation: NormalisationConfig = NormalisationConfig()
    evidence: EvidenceConfig = EvidenceConfig()
    priority: PriorityConfig = PriorityConfig()
    capacity: CapacityConfig = CapacityConfig()
    route: RouteConfig = RouteConfig()
    optimiser: OptimiserConfig = OptimiserConfig()
    confidence: ConfidenceConfig = ConfidenceConfig()
    generation: GenerationConfig = GenerationConfig()
    validation: ValidationConfig = ValidationConfig()

    def constants(self) -> list[Constant]:
        """Flatten every constant in the tree for the UI transparency table."""
        return sorted(_walk_constants(self), key=lambda c: c.key)


def _walk_constants(node: object) -> Iterator[Constant]:
    if isinstance(node, Constant):
        yield node
        return
    if isinstance(node, BaseModel):
        for value in dict(node).values():
            yield from _walk_constants(value)
        return
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk_constants(value)
        return
    if isinstance(node, (list, tuple)):
        for value in node:
            yield from _walk_constants(value)


MODEL_CONFIG = AstraModelConfig()
"""Process-wide singleton. Engines import this; they never hardcode a number."""
