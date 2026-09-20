"""Raster connectors: elevation, land cover and population.

All three are read as windowed clips of the published source, so ASTRA vendors
only the study corridor rather than gigabytes of national coverage. The clip
extent, the source URL and the processing step are recorded in the provenance
registry, because "we clipped it" is itself something a reviewer is entitled to
see stated.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

from astra.data.connectors.base import Connector, ConnectorError, FetchResult
from astra.domain.enums import ConfidenceBand, ProvenanceClass
from astra.domain.models import BBox, DatasetRecord

# GDAL settings for efficient range reads against cloud-optimised GeoTIFFs.
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.tiff")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")


def _clip_remote(
    url: str,
    bbox: BBox,
    destination: Path,
    *,
    dtype: str,
    nodata: float,
    transform_values=None,
) -> int:
    """Windowed-read a remote raster over the bbox and write the clip locally."""
    try:
        with rasterio.open(url) as source:
            window = from_bounds(
                bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat, source.transform
            )
            data = source.read(1, window=window)
            transform = source.window_transform(window)
            crs = source.crs
            source_nodata = source.nodata
    except rasterio.errors.RasterioIOError as exc:
        raise ConnectorError(f"could not read {url}: {exc}") from exc

    data = np.asarray(data, dtype="float64")
    if source_nodata is not None:
        data = np.where(data == source_nodata, np.nan, data)
    if transform_values is not None:
        data = transform_values(data)

    filled = np.where(np.isnan(data), nodata, data).astype(dtype)

    profile = {
        "driver": "GTiff",
        "height": filled.shape[0],
        "width": filled.shape[1],
        "count": 1,
        "dtype": dtype,
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
        "compress": "deflate",
        "predictor": 2,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    with rasterio.open(destination, "w", **profile) as sink:
        sink.write(filled, 1)
    return destination.stat().st_size


class CopernicusDemConnector(Connector):
    """Copernicus DEM GLO-30, the elevation model every terrain derivative uses."""

    dataset_id = "copernicus-dem-30m"
    TILE = "Copernicus_DSM_COG_10_N30_00_E079_00_DEM"
    SOURCE_URL = (
        "https://copernicus-dem-30m.s3.amazonaws.com/"
        f"{TILE}/{TILE}.tif"
    )

    @property
    def artifact_path(self) -> Path:
        return self.raw_dir / "dem" / "copernicus_dem_30m_alaknanda.tif"

    def fetch(self, *, force: bool = False) -> FetchResult:
        if not self._prepare(force):
            return self._cached_result()
        written = _clip_remote(
            f"/vsicurl/{self.SOURCE_URL}",
            self.bbox,
            self.artifact_path,
            dtype="int16",
            nodata=-32768,
            transform_values=np.round,
        )
        return FetchResult(
            dataset_id=self.dataset_id,
            path=self.artifact_path,
            bytes_written=written,
            from_cache=False,
            detail=f"windowed clip of tile {self.TILE} to the study bbox",
        )

    def dataset_record(self, acquired: date) -> DatasetRecord:
        return DatasetRecord(
            id=self.dataset_id,
            name="Copernicus DEM GLO-30 (study corridor clip)",
            source="European Space Agency / Copernicus, via the AWS Open Data registry",
            source_url=self.SOURCE_URL,
            provenance=ProvenanceClass.REAL_OPEN,
            acquired=acquired,
            processing=(
                "Windowed read of the published cloud-optimised GeoTIFF clipped to the "
                "study bounding box and stored as integer metres. Values unmodified."
            ),
            resolution="30 m (1 arc-second)",
            temporal_coverage="2011-2015 acquisition, 2021 release",
            confidence=ConfidenceBand.HIGH,
            licence="Copernicus DEM open licence, free use with attribution",
            notes=(
                "Elevation drives slope, ruggedness, drainage, HAND and catchment "
                "steepness. It is the single most load-bearing real input in ASTRA."
            ),
        )


class WorldCoverConnector(Connector):
    """ESA WorldCover 10 m land cover - the real product behind usable-area estimation."""

    dataset_id = "esa-worldcover-10m"
    TILE = "N30E078"
    SOURCE_URL = (
        "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
        f"ESA_WorldCover_10m_2021_v200_{TILE}_Map.tif"
    )

    @property
    def artifact_path(self) -> Path:
        return self.raw_dir / "landcover" / "esa_worldcover_2021_alaknanda.tif"

    def fetch(self, *, force: bool = False) -> FetchResult:
        if not self._prepare(force):
            return self._cached_result()
        written = _clip_remote(
            f"/vsicurl/{self.SOURCE_URL}",
            self.bbox,
            self.artifact_path,
            dtype="uint8",
            nodata=0,
        )
        return FetchResult(
            dataset_id=self.dataset_id,
            path=self.artifact_path,
            bytes_written=written,
            from_cache=False,
            detail=f"windowed clip of tile {self.TILE} to the study bbox",
        )

    def dataset_record(self, acquired: date) -> DatasetRecord:
        return DatasetRecord(
            id=self.dataset_id,
            name="ESA WorldCover 2021 v200 (study corridor clip)",
            source="European Space Agency WorldCover, via the AWS Open Data registry",
            source_url=self.SOURCE_URL,
            provenance=ProvenanceClass.REAL_OPEN,
            acquired=acquired,
            processing=(
                "Windowed read of the published GeoTIFF clipped to the study bounding "
                "box. Class codes unmodified; reclassification to buildable and "
                "protected happens downstream and is recorded separately."
            ),
            resolution="10 m",
            temporal_coverage="2021",
            confidence=ConfidenceBand.HIGH,
            licence="CC BY 4.0",
            notes=(
                "Used for the land-cover suitability gate and as the primary path for "
                "usable-area estimation, so no land-cover model needs to be trained."
            ),
        )


class WorldPopConnector(Connector):
    """WorldPop 1 km population counts - the real anchor for settlement sizing."""

    dataset_id = "worldpop-1km-2020"
    SOURCE_URL = (
        "https://data.worldpop.org/GIS/Population/Global_2000_2020_1km_UNadj/2020/IND/"
        "ind_ppp_2020_1km_Aggregated_UNadj.tif"
    )

    @property
    def artifact_path(self) -> Path:
        return self.raw_dir / "population" / "worldpop_1km_2020_alaknanda.tif"

    @property
    def _download_path(self) -> Path:
        return self.raw_dir / "population" / ".worldpop_ind_1km_2020.tif"

    def fetch(self, *, force: bool = False) -> FetchResult:
        if not self._prepare(force):
            return self._cached_result()

        # This server does not support HTTP range requests, so the national file
        # is downloaded once, clipped, and the download discarded.
        import requests

        self._download_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with requests.get(self.SOURCE_URL, stream=True, timeout=300) as response:
                response.raise_for_status()
                with self._download_path.open("wb") as sink:
                    for chunk in response.iter_content(chunk_size=1 << 20):
                        sink.write(chunk)
        except Exception as exc:  # noqa: BLE001 - reported as a connector failure
            raise ConnectorError(f"could not download {self.SOURCE_URL}: {exc}") from exc

        written = _clip_remote(
            str(self._download_path),
            self.bbox,
            self.artifact_path,
            dtype="float32",
            nodata=-9999.0,
        )
        self._download_path.unlink(missing_ok=True)
        return FetchResult(
            dataset_id=self.dataset_id,
            path=self.artifact_path,
            bytes_written=written,
            from_cache=False,
            detail="national raster downloaded once, clipped to the study bbox, discarded",
        )

    def dataset_record(self, acquired: date) -> DatasetRecord:
        return DatasetRecord(
            id=self.dataset_id,
            name="WorldPop India 1 km population, UN-adjusted 2020 (study corridor clip)",
            source="WorldPop, University of Southampton",
            source_url=self.SOURCE_URL,
            provenance=ProvenanceClass.REAL_OPEN,
            acquired=acquired,
            processing=(
                "National 1 km population count raster downloaded once and clipped to "
                "the study bounding box. Counts unmodified."
            ),
            resolution="1 km",
            temporal_coverage="2020, UN-adjusted",
            confidence=ConfidenceBand.MEDIUM,
            licence="CC BY 4.0",
            notes=(
                "Used to calibrate the size of synthetic habitations to the real "
                "population actually present in each part of the corridor. It is a "
                "modelled population surface, not a census enumeration, and the "
                "habitation records built from it remain synthetic."
            ),
        )
