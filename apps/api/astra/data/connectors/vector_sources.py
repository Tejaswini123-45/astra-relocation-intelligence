"""Vector and tabular connectors: roads, historical incidents and rainfall.

Each vendors a single JSON or CSV artifact under ``data/raw`` and is read back
offline. Filtering is done at fetch time and stated in the provenance record, so
what is on disk is exactly what the engines see.
"""

from __future__ import annotations

import csv
import io
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests

from astra.data.connectors.base import Connector, ConnectorError, FetchResult
from astra.domain.enums import ConfidenceBand, ProvenanceClass
from astra.domain.models import BBox, DatasetRecord

REQUEST_TIMEOUT = 180
USER_AGENT = "ASTRA-SIH26191/0.1 (disaster-management research prototype)"
RETRIES = 3


def _request(method: str, url: str, **kwargs: Any) -> requests.Response:
    """One HTTP call with a stated user agent and bounded retries.

    Public open-data endpoints throttle and occasionally stall. Retrying here
    keeps the one-time ingest robust; nothing at demo time uses this path.
    """
    headers = {"user-agent": USER_AGENT, **kwargs.pop("headers", {})}
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            response = requests.request(
                method, url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs
            )
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(5 * (attempt + 1))
    raise ConnectorError(f"{url} failed after {RETRIES} attempts: {last}")


class OverpassConnector(Connector):
    """OpenStreetMap roads, bridges and waterways for the study corridor."""

    dataset_id = "osm-extract"
    ENDPOINTS = (
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    )
    SOURCE_URL = "https://overpass-api.de/api/interpreter"

    def _query(self) -> str:
        bbox = f"{self.bbox.min_lat},{self.bbox.min_lon},{self.bbox.max_lat},{self.bbox.max_lon}"
        return f"""
[out:json][timeout:120];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|track|road)$"]({bbox});
  way["waterway"~"^(river|stream)$"]({bbox});
  way["man_made"="bridge"]({bbox});
);
out body geom;
"""

    @property
    def artifact_path(self) -> Path:
        return self.raw_dir / "osm" / "osm_alaknanda_network.json"

    def fetch(self, *, force: bool = False) -> FetchResult:
        if not self._prepare(force):
            return self._cached_result()

        last_error: Exception | None = None
        for endpoint in self.ENDPOINTS:
            try:
                response = _request("POST", endpoint, data={"data": self._query()})
                payload = response.json()
            except Exception as exc:  # noqa: BLE001 - reported as a connector failure
                last_error = exc
                continue
            if "elements" not in payload:
                last_error = ConnectorError(f"unexpected Overpass payload from {endpoint}")
                continue
            self.artifact_path.write_text(json.dumps(payload), encoding="utf-8")
            highways = sum(
                1 for e in payload["elements"] if e.get("tags", {}).get("highway")
            )
            waterways = sum(
                1 for e in payload["elements"] if e.get("tags", {}).get("waterway")
            )
            return FetchResult(
                dataset_id=self.dataset_id,
                path=self.artifact_path,
                bytes_written=self.artifact_path.stat().st_size,
                from_cache=False,
                detail=f"{highways} highway ways, {waterways} waterway ways",
            )
        raise ConnectorError(f"all Overpass endpoints failed: {last_error}")

    def load(self) -> dict[str, Any]:
        return json.loads(self.require_cached().read_text(encoding="utf-8"))

    def dataset_record(self, acquired: date) -> DatasetRecord:
        return DatasetRecord(
            id=self.dataset_id,
            name="OpenStreetMap road and waterway network (study corridor extract)",
            source="OpenStreetMap contributors, via the Overpass API",
            source_url=self.SOURCE_URL,
            provenance=ProvenanceClass.REAL_OPEN,
            acquired=acquired,
            processing=(
                "Overpass query for highway, waterway and bridge ways intersecting the "
                "study bounding box, with full geometry, stored verbatim."
            ),
            resolution="vector, community-surveyed",
            temporal_coverage="live database, snapshot on the acquisition date",
            confidence=ConfidenceBand.MEDIUM,
            licence="Open Database Licence (ODbL) 1.0",
            notes=(
                "Coverage in mountain districts is uneven. Route reliability figures "
                "inherit that uncertainty and it is carried into the confidence model."
            ),
        )


class LandslideInventoryConnector(Connector):
    """NASA Global Landslide Catalog - the historical evidence and the validation set."""

    dataset_id = "landslide-inventory"
    SOURCE_URL = (
        "https://data.nasa.gov/docs/legacy/Global_Landslide_Catalog_Export/"
        "Global_Landslide_Catalog_Export_rows.csv"
    )
    # Incidents are retained for the wider Uttarakhand-Himalaya window, not just the
    # study bbox: kernel density at the corridor edge needs neighbouring evidence,
    # and the back-test needs enough points to mean anything.
    REGION = BBox(min_lon=77.5, min_lat=28.7, max_lon=81.1, max_lat=31.5)

    @property
    def artifact_path(self) -> Path:
        return self.raw_dir / "incidents" / "nasa_glc_uttarakhand.csv"

    def fetch(self, *, force: bool = False) -> FetchResult:
        if not self._prepare(force):
            return self._cached_result()
        response = _request("GET", self.SOURCE_URL)

        reader = csv.DictReader(io.StringIO(response.text))
        fieldnames = reader.fieldnames or []
        kept: list[dict[str, str]] = []
        for row in reader:
            try:
                lat = float(row.get("latitude") or "nan")
                lon = float(row.get("longitude") or "nan")
            except ValueError:
                continue
            if (
                self.REGION.min_lat <= lat <= self.REGION.max_lat
                and self.REGION.min_lon <= lon <= self.REGION.max_lon
            ):
                kept.append(row)

        with self.artifact_path.open("w", encoding="utf-8", newline="") as sink:
            writer = csv.DictWriter(sink, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(kept)

        return FetchResult(
            dataset_id=self.dataset_id,
            path=self.artifact_path,
            bytes_written=self.artifact_path.stat().st_size,
            from_cache=False,
            detail=f"{len(kept)} incidents retained inside the Uttarakhand-Himalaya window",
        )

    def load(self) -> list[dict[str, str]]:
        with self.require_cached().open(encoding="utf-8", newline="") as source:
            return list(csv.DictReader(source))

    def dataset_record(self, acquired: date) -> DatasetRecord:
        return DatasetRecord(
            id=self.dataset_id,
            name="NASA Global Landslide Catalog (Uttarakhand-Himalaya subset)",
            source="NASA Goddard Space Flight Center, Global Landslide Catalog",
            source_url=self.SOURCE_URL,
            provenance=ProvenanceClass.REAL_OPEN,
            acquired=acquired,
            processing=(
                "Global export filtered to the Uttarakhand-Himalaya window "
                f"{REGION_STR}. Rows unmodified."
            ),
            resolution="point events with a stated location accuracy field",
            temporal_coverage="2007 onwards, as published",
            confidence=ConfidenceBand.MEDIUM,
            licence="NASA open data, attribution requested",
            notes=(
                "Reported-event inventories are spatially biased towards roads, "
                "settlements and media attention. That bias caps what the back-test "
                "can prove and is stated wherever the validation result is shown."
            ),
        )


REGION_STR = "77.5E-81.1E, 28.7N-31.5N"


class RainfallConnector(Connector):
    """ERA5 reanalysis daily precipitation over a grid covering the corridor."""

    dataset_id = "rainfall-gridded"
    SOURCE_URL = "https://archive-api.open-meteo.com/v1/archive"
    START = "2015-01-01"
    END = "2024-12-31"
    GRID_STEPS = 4  # 4 x 4 sample points across the study bbox

    @property
    def artifact_path(self) -> Path:
        return self.raw_dir / "rainfall" / "open_meteo_era5_alaknanda.json"

    def _grid(self) -> list[tuple[float, float]]:
        lats = [
            self.bbox.min_lat
            + (self.bbox.max_lat - self.bbox.min_lat) * (i + 0.5) / self.GRID_STEPS
            for i in range(self.GRID_STEPS)
        ]
        lons = [
            self.bbox.min_lon
            + (self.bbox.max_lon - self.bbox.min_lon) * (j + 0.5) / self.GRID_STEPS
            for j in range(self.GRID_STEPS)
        ]
        return [(lat, lon) for lat in lats for lon in lons]

    def fetch(self, *, force: bool = False) -> FetchResult:
        if not self._prepare(force):
            return self._cached_result()
        points = self._grid()
        params = {
            "latitude": ",".join(f"{lat:.4f}" for lat, _ in points),
            "longitude": ",".join(f"{lon:.4f}" for _, lon in points),
            "start_date": self.START,
            "end_date": self.END,
            "daily": "precipitation_sum",
            "timezone": "UTC",
        }
        payload = _request("GET", self.SOURCE_URL, params=params).json()

        if isinstance(payload, dict):
            payload = [payload]
        self.artifact_path.write_text(json.dumps(payload), encoding="utf-8")
        days = len(payload[0]["daily"]["time"]) if payload else 0
        return FetchResult(
            dataset_id=self.dataset_id,
            path=self.artifact_path,
            bytes_written=self.artifact_path.stat().st_size,
            from_cache=False,
            detail=f"{len(payload)} grid points x {days} daily records",
        )

    def load(self) -> list[dict[str, Any]]:
        return json.loads(self.require_cached().read_text(encoding="utf-8"))

    def dataset_record(self, acquired: date) -> DatasetRecord:
        return DatasetRecord(
            id=self.dataset_id,
            name="ERA5 daily precipitation over the study corridor (Open-Meteo archive)",
            source="ECMWF ERA5 reanalysis, served by the Open-Meteo historical archive",
            source_url=self.SOURCE_URL,
            provenance=ProvenanceClass.REAL_OPEN,
            acquired=acquired,
            processing=(
                f"Daily precipitation totals {self.START} to {self.END} retrieved for a "
                f"{self.GRID_STEPS}x{self.GRID_STEPS} grid across the study bounding box "
                "and stored verbatim."
            ),
            resolution="approximately 25 km reanalysis grid, sampled at 16 points",
            temporal_coverage=f"{self.START} to {self.END}",
            confidence=ConfidenceBand.MEDIUM,
            licence="ERA5: Copernicus licence. Open-Meteo: CC BY 4.0",
            notes=(
                "Reanalysis smooths the extreme short-duration rainfall that actually "
                "triggers cloudbursts in this terrain, so intensity is a conservative "
                "proxy rather than a gauge measurement."
            ),
        )
