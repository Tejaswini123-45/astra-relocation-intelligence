"""Source connectors. Fetch once, vendor locally, never call out at demo time."""

from __future__ import annotations

from pathlib import Path

from astra.data.connectors.base import Connector, ConnectorError, FetchResult
from astra.data.connectors.imagery import SentinelCompositeConnector
from astra.data.connectors.raster_sources import (
    CopernicusDemConnector,
    WorldCoverConnector,
    WorldPopConnector,
)
from astra.data.connectors.vector_sources import (
    LandslideInventoryConnector,
    OverpassConnector,
    RainfallConnector,
)
from astra.domain.models import BBox

CONNECTOR_TYPES: tuple[type[Connector], ...] = (
    CopernicusDemConnector,
    WorldCoverConnector,
    WorldPopConnector,
    OverpassConnector,
    LandslideInventoryConnector,
    RainfallConnector,
    SentinelCompositeConnector,
)


def build_connectors(raw_dir: Path, bbox: BBox) -> list[Connector]:
    return [connector(raw_dir, bbox) for connector in CONNECTOR_TYPES]


__all__ = [
    "CONNECTOR_TYPES",
    "Connector",
    "ConnectorError",
    "CopernicusDemConnector",
    "FetchResult",
    "LandslideInventoryConnector",
    "OverpassConnector",
    "RainfallConnector",
    "SentinelCompositeConnector",
    "WorldCoverConnector",
    "WorldPopConnector",
    "build_connectors",
]
