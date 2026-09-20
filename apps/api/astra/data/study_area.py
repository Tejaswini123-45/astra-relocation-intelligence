"""The study area: the Alaknanda valley corridor, Chamoli district, Uttarakhand.

The geography here is real (CLAUDE.md section 4.1): terrain, rivers, roads,
elevation and historical incident points come from open datasets for this
bounding box. What is *not* real, and is labelled as such everywhere it appears,
is the habitation and candidate-site layer: those are synthetic, fictional and
terrain-calibrated, so that ASTRA never renders a hazard classification over a
real named village.
"""

from __future__ import annotations

from astra.domain.models import BBox, GeoPoint, StudyArea

CHAMOLI_ALAKNANDA = StudyArea(
    id="chamoli-alaknanda",
    name="Alaknanda Valley Corridor",
    district="Chamoli",
    state="Uttarakhand",
    # Corridor along the Alaknanda between Karnaprayag and Joshimath.
    bbox=BBox(min_lon=79.30, min_lat=30.30, max_lon=79.75, max_lat=30.65),
    centre=GeoPoint(lon=79.525, lat=30.475),
    default_zoom=11.0,
    description=(
        "A Himalayan settlement corridor with genuine slope, rainfall, flood and "
        "access interaction, a documented multi-hazard history and an active "
        "relocation policy context."
    ),
)

STUDY_AREAS: dict[str, StudyArea] = {CHAMOLI_ALAKNANDA.id: CHAMOLI_ALAKNANDA}

DEFAULT_STUDY_AREA_ID = CHAMOLI_ALAKNANDA.id


def get_study_area(study_area_id: str | None = None) -> StudyArea:
    return STUDY_AREAS[study_area_id or DEFAULT_STUDY_AREA_ID]
