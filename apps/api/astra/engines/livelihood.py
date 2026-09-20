"""Livelihood disruption: what moving costs a household beyond the move itself.

A relocation that puts people somewhere safe and somewhere they cannot work has
not solved the problem. This module computes how much a given destination
disrupts a given habitation's livelihood, as a weighted composite of four
measured quantities:

* **Travel back to the livelihood centre.** Routed, not straight-line: the road
  from the new site to where these people actually work or trade.
* **Connectivity class of that link.** A track and a state highway are not the
  same road even at the same travel time.
* **Reliability of that link.** A connection that fails every monsoon is not a
  connection.
* **Market and service access at the destination.** Routed travel time from the
  site to the nearest trunk road, which is where the district's markets, banks
  and offices are.

**There is no fixed kilometre rule here and there never was.** CLAUDE.md section
5.6 is explicit about that, and the reason is that a five-kilometre move down a
valley road and a five-kilometre move over a ridge are different events. Every
component below is measured on the real network and every weight is served on
the API with its own description.
"""

from __future__ import annotations

from dataclasses import dataclass

from astra.domain.enums import ProvenanceClass, RoadClass
from astra.domain.model_config import MODEL_CONFIG, AstraModelConfig
from astra.domain.models import FactorContribution

FORMULA_LIVELIHOOD = "optimiser.livelihood_disruption"

#: How disruptive it is to depend on each road class, worst to best. A household
#: whose only link to its work is an unsurfaced track is more exposed than one on
#: a state highway, at any travel time.
CONNECTIVITY_DISRUPTION: dict[RoadClass, float] = {
    RoadClass.NATIONAL_HIGHWAY: 0.0,
    RoadClass.STATE_HIGHWAY: 0.2,
    RoadClass.DISTRICT_ROAD: 0.45,
    RoadClass.VILLAGE_ROAD: 0.7,
    RoadClass.TRACK: 1.0,
}


@dataclass(frozen=True)
class LivelihoodDisruption:
    """One habitation-to-site disruption figure, with its four components."""

    habitation_id: str
    site_id: str
    value: float
    factors: list[FactorContribution]
    commute_min: float
    commute_reliability: float
    worst_road_class: RoadClass
    market_access_min: float
    reachable: bool

    @property
    def percent(self) -> float:
        return round(self.value * 100.0, 1)


def _normalise(value: float, ceiling: float) -> float:
    if ceiling <= 0:
        return 0.0
    return min(max(value / ceiling, 0.0), 1.0)


def livelihood_disruption(
    *,
    habitation_id: str,
    site_id: str,
    commute_min: float,
    commute_reliability: float,
    worst_road_class: RoadClass,
    market_access_min: float,
    reachable: bool = True,
    config: AstraModelConfig | None = None,
) -> LivelihoodDisruption:
    """The weighted composite, with every term kept and reported.

    ``commute_min`` is the routed travel time from the destination site back to
    the habitation's livelihood centre; ``market_access_min`` the routed travel
    time from the site to the nearest trunk road. Both come from Engine 5.

    An unreachable livelihood centre scores the maximum on every component
    rather than being dropped: a site people cannot get back to work from is the
    most disruptive destination there is, not a missing measurement.
    """
    settings = (config or MODEL_CONFIG).optimiser
    if not reachable:
        factors = [
            FactorContribution(
                factor="livelihood_centre_unreachable",
                raw_value=None,
                normalised_value=1.0,
                weight=1.0,
                contribution=1.0,
                provenance=ProvenanceClass.DERIVED,
            )
        ]
        return LivelihoodDisruption(
            habitation_id=habitation_id,
            site_id=site_id,
            value=1.0,
            factors=factors,
            commute_min=0.0,
            commute_reliability=0.0,
            worst_road_class=worst_road_class,
            market_access_min=0.0,
            reachable=False,
        )

    commute_ceiling = settings.livelihood_commute_ceiling_min.value
    market_ceiling = settings.livelihood_market_ceiling_min.value

    terms = (
        (
            "commute_to_livelihood_centre",
            settings.livelihood_w_travel_time,
            commute_min,
            _normalise(commute_min, commute_ceiling),
            "min",
        ),
        (
            "connectivity_class_of_link",
            settings.livelihood_w_connectivity,
            None,
            CONNECTIVITY_DISRUPTION[worst_road_class],
            None,
        ),
        (
            "reliability_of_link",
            settings.livelihood_w_road_reliability,
            commute_reliability,
            1.0 - min(max(commute_reliability, 0.0), 1.0),
            None,
        ),
        (
            "market_access_at_destination",
            settings.livelihood_w_market_access,
            market_access_min,
            _normalise(market_access_min, market_ceiling),
            "min",
        ),
    )

    factors: list[FactorContribution] = []
    total = 0.0
    for factor, weight, measured, normalised, unit in terms:
        normalised = round(min(max(normalised, 0.0), 1.0), 6)
        contribution = round(weight.value * normalised, 6)
        total += contribution
        factors.append(
            FactorContribution(
                factor=factor,
                raw_value=None if measured is None else round(measured, 4),
                normalised_value=normalised,
                weight=round(weight.value, 6),
                contribution=contribution,
                provenance=ProvenanceClass.DERIVED,
                unit=unit,
            )
        )

    return LivelihoodDisruption(
        habitation_id=habitation_id,
        site_id=site_id,
        value=round(min(max(total, 0.0), 1.0), 4),
        factors=factors,
        commute_min=round(commute_min, 1),
        commute_reliability=round(commute_reliability, 4),
        worst_road_class=worst_road_class,
        market_access_min=round(market_access_min, 1),
        reachable=True,
    )
