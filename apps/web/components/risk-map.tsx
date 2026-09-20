"use client";

import { MapboxOverlay } from "@deck.gl/mapbox";
import { BitmapLayer, GeoJsonLayer, ScatterplotLayer } from "@deck.gl/layers";
import maplibregl, { type Map as MapLibreMap } from "maplibre-gl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { CandidateSite, Habitation, StudyArea, ZoneFeature } from "@astra/contracts";

import "maplibre-gl/dist/maplibre-gl.css";

/**
 * The geospatial command surface.
 *
 * MapLibre needs no access token, so nothing here can expire or leak mid-demo,
 * and the basemap is ASTRA's own shaded relief served by the API - the map keeps
 * working with the network unplugged. deck.gl carries the hazard overlay and the
 * zone geometry on the GPU in the viewer's browser.
 *
 * Nothing on this map is decorative. Every layer is a rendering of a value the
 * API computed, and clicking anywhere asks the API to take that value apart.
 */

export type LayerToggles = {
  hazard: boolean;
  zones: boolean;
  habitations: boolean;
  sites: boolean;
  roads: boolean;
  /** The routed graph, coloured by each segment's computed failure probability. */
  network?: boolean;
  /**
   * The evidence-confidence surface, hatched. Drawn *over* the hazard layer
   * rather than blended into it: confidence is never multiplied into
   * susceptibility, and a layer that faded the hazard where evidence is thin
   * would read as "less dangerous here" when it means "less certain here".
   */
  confidence?: boolean;
};

/**
 * A circle of ground to outline: the footprint of a live observation.
 *
 * Drawn at the radius the engine actually used, so the circle on the map is the
 * ground that was re-scored rather than a decorative pulse near the point.
 */
export type MapCircle = {
  id: string;
  lon: number;
  lat: number;
  radius_m: number;
  colour: string;
};

/** A route to draw over the network, with the segments that make it fragile. */
export type DrawnRoute = {
  id: string;
  geometry: number[][];
  colour: [number, number, number, number];
  width: number;
  pointsOfFailure?: { segment_id: string }[];
};

/**
 * Failure probability to colour. Teal is road ASTRA expects to hold; red is road
 * it does not. The break points are the same ones the route panel quotes, so a
 * segment that reads red here reads red there.
 */
function failureColour(p: number): [number, number, number, number] {
  if (p >= 0.08) return [201, 66, 56, 225];
  if (p >= 0.04) return [206, 122, 58, 210];
  if (p >= 0.015) return [193, 165, 79, 185];
  return [86, 158, 148, 165];
}

/** The few semantic colours a caller may name, as deck.gl RGBA. */
const TOKEN_RGB: Record<string, [number, number, number]> = {
  "var(--color-signal)": [104, 154, 214],
  "var(--color-critical)": [201, 66, 56],
  "var(--color-warning)": [206, 122, 58],
  "var(--color-safe)": [86, 190, 172],
  "var(--color-neutral)": [130, 142, 162],
};

function withAlpha(token: string, alpha: number): [number, number, number, number] {
  const rgb = TOKEN_RGB[token] ?? TOKEN_RGB["var(--color-signal)"];
  return [rgb[0], rgb[1], rgb[2], alpha];
}

const ZONE_FILL: Record<string, [number, number, number, number]> = {
  CRITICAL: [192, 57, 47, 130],
  ELEVATED: [205, 117, 56, 105],
  WATCH: [185, 147, 64, 45],
  LOW: [42, 111, 102, 60],
};

const ZONE_LINE: Record<string, [number, number, number, number]> = {
  CRITICAL: [224, 96, 84, 230],
  ELEVATED: [226, 148, 84, 210],
  WATCH: [214, 180, 104, 120],
  LOW: [86, 158, 148, 150],
};

function baseStyle(studyArea: StudyArea, terrainUrl: string): maplibregl.StyleSpecification {
  const { min_lon, min_lat, max_lon, max_lat } = studyArea.bbox;
  return {
    version: 8,
    // No sprite or glyph server: everything the style needs is served by ASTRA
    // itself, so the map renders with no internet connection at all.
    sources: {
      terrain: {
        type: "image",
        url: terrainUrl,
        coordinates: [
          [min_lon, max_lat],
          [max_lon, max_lat],
          [max_lon, min_lat],
          [min_lon, min_lat],
        ],
      },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#070b12" } },
      {
        id: "terrain",
        type: "raster",
        source: "terrain",
        paint: { "raster-opacity": 0.92, "raster-fade-duration": 0 },
      },
    ],
  } as maplibregl.StyleSpecification;
}

export function RiskMap({
  studyArea,
  terrainUrl,
  overlayUrl,
  confidenceUrl,
  roadsUrl,
  networkUrl,
  drawnRoutes,
  closedSegments,
  circles,
  onSelectSegment,
  zones,
  habitations,
  sites,
  toggles,
  hazardOpacity,
  selected,
  onSelectPoint,
  onSelectZone,
  habitationColour,
  highlightId,
  focusBounds,
  onReady,
}: {
  /** Called once, when the terrain basemap has loaded and layers can draw. */
  onReady?: () => void;
  studyArea: StudyArea;
  terrainUrl: string;
  overlayUrl: string;
  /** The hatched confidence surface, aligned to the same bounding box. */
  confidenceUrl?: string;
  roadsUrl: string;
  /** The routed graph as GeoJSON. Omitted on screens that do not route. */
  networkUrl?: string;
  /** Routes to draw on top of the network, in draw order. */
  drawnRoutes?: DrawnRoute[];
  /** Segments closed in the current scenario, drawn as struck out. */
  closedSegments?: string[];
  /** Observation footprints to outline, at the radius the engine re-scored. */
  circles?: MapCircle[];
  onSelectSegment?: (segmentId: string) => void;
  zones: ZoneFeature[];
  habitations: Habitation[];
  sites: CandidateSite[];
  toggles: LayerToggles;
  hazardOpacity: number;
  selected: { lon: number; lat: number } | null;
  onSelectPoint: (lon: number, lat: number) => void;
  onSelectZone: (zoneId: string | null) => void;
  /** Optional per-habitation colour, so a screen can encode phase or rank. */
  habitationColour?: (habitation: Habitation) => [number, number, number, number];
  /** Habitation to ring, when a list selection drives the map. */
  highlightId?: string | null;
  /**
   * Bounds to ease the camera to. A 4 km route inside a 40 km corridor is a
   * thread on the screen otherwise, so a screen that selects a route says where
   * to look. Passing null returns the camera to the study area.
   */
  focusBounds?: [[number, number], [number, number]] | null;
}) {
  const container = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  // The map is created once. Its click handler is read through a ref so that a
  // caller passing an inline arrow - which every caller does - cannot land in
  // the creation effect's dependencies and tear the whole MapLibre instance
  // down and rebuild it on every render. That bug is invisible until you notice
  // the camera resetting itself, and expensive long before you do.
  const onSelectPointRef = useRef(onSelectPoint);
  onSelectPointRef.current = onSelectPoint;
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;
  const overlayRef = useRef<MapboxOverlay | null>(null);
  const [ready, setReady] = useState(false);
  const [roads, setRoads] = useState<GeoJSON.FeatureCollection | null>(null);
  const [network, setNetwork] = useState<GeoJSON.FeatureCollection | null>(null);

  const bounds = useMemo(
    () =>
      [
        [studyArea.bbox.min_lon, studyArea.bbox.min_lat],
        [studyArea.bbox.max_lon, studyArea.bbox.max_lat],
      ] as [[number, number], [number, number]],
    [studyArea],
  );

  useEffect(() => {
    let cancelled = false;
    fetch(roadsUrl)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!cancelled) setRoads(data);
      })
      .catch(() => setRoads(null));
    return () => {
      cancelled = true;
    };
  }, [roadsUrl]);

  useEffect(() => {
    if (!networkUrl) return;
    let cancelled = false;
    fetch(networkUrl)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!cancelled) setNetwork(data);
      })
      .catch(() => setNetwork(null));
    return () => {
      cancelled = true;
    };
  }, [networkUrl]);

  useEffect(() => {
    if (!container.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      style: baseStyle(studyArea, terrainUrl),
      bounds,
      fitBoundsOptions: { padding: 24 },
      maxBounds: [
        [studyArea.bbox.min_lon - 0.15, studyArea.bbox.min_lat - 0.12],
        [studyArea.bbox.max_lon + 0.15, studyArea.bbox.max_lat + 0.12],
      ],
      attributionControl: false,
      dragRotate: false,
      maxPitch: 0,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: "metric" }), "bottom-right");
    const overlay = new MapboxOverlay({ interleaved: false, layers: [] });
    map.addControl(overlay);
    map.on("load", () => {
      setReady(true);
      onReadyRef.current?.();
    });
    map.on("click", (event) => {
      onSelectPointRef.current(event.lngLat.lng, event.lngLat.lat);
    });
    mapRef.current = map;
    overlayRef.current = overlay;
    return () => {
      map.remove();
      mapRef.current = null;
      overlayRef.current = null;
    };
  }, [bounds, studyArea, terrainUrl]);

  // MapLibre sizes its canvas once, from the container as it was at creation.
  // Every screen here puts the map inside a CSS grid that settles after mount -
  // and on a screen whose panels grow as data arrives, it settles more than
  // once. Without this the canvas keeps its first size and the terrain sits in
  // a corner of its own container, which looks exactly like a broken map.
  useEffect(() => {
    const map = mapRef.current;
    const element = container.current;
    if (!ready || !map || !element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => map.resize());
    observer.observe(element);
    map.resize();
    return () => observer.disconnect();
  }, [ready]);

  const focusKey = focusBounds ? focusBounds.flat().join(",") : "";
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    map.fitBounds(focusKey ? (focusBounds as [[number, number], [number, number]]) : bounds, {
      padding: focusKey ? 90 : 24,
      duration: 700,
      maxZoom: 13.5,
    });
  }, [ready, focusKey, focusBounds, bounds]);

  const handleZoneClick = useCallback(
    (info: { object?: ZoneFeature }) => {
      onSelectZone(info.object?.properties.id ?? null);
      return true;
    },
    [onSelectZone],
  );

  const closedKey = (closedSegments ?? []).join(",");
  const closed = useMemo(
    () => new Set(closedKey ? closedKey.split(",") : []),
    [closedKey],
  );

  useEffect(() => {
    if (!ready || !overlayRef.current) return;
    const { min_lon, min_lat, max_lon, max_lat } = studyArea.bbox;

    const layers = [
      toggles.hazard &&
        new BitmapLayer({
          id: "hazard-composite",
          image: overlayUrl,
          bounds: [min_lon, min_lat, max_lon, max_lat],
          opacity: hazardOpacity,
          pickable: false,
        }),
      toggles.roads &&
        roads &&
        new GeoJsonLayer({
          id: "roads",
          data: roads,
          stroked: true,
          filled: false,
          getLineColor: (feature: GeoJSON.Feature) =>
            feature.properties?.kind === "waterway"
              ? [88, 132, 176, 150]
              : [190, 202, 220, 120],
          getLineWidth: (feature: GeoJSON.Feature) =>
            feature.properties?.kind === "waterway" ? 18 : 26,
          lineWidthMinPixels: 0.6,
          lineWidthMaxPixels: 3,
          pickable: false,
        }),
      toggles.network &&
        network &&
        new GeoJsonLayer({
          id: "route-network",
          data: network,
          stroked: true,
          filled: false,
          getLineColor: (feature: GeoJSON.Feature) => {
            const id = String(feature.properties?.segment_id);
            if (closed.has(id)) return [120, 128, 142, 210];
            return failureColour(Number(feature.properties?.p_fail ?? 0));
          },
          getLineWidth: (feature: GeoJSON.Feature) =>
            feature.properties?.is_bridge ? 60 : 34,
          lineWidthMinPixels: 1,
          lineWidthMaxPixels: 5,
          updateTriggers: { getLineColor: [closedKey] },
          pickable: Boolean(onSelectSegment),
          onClick: (info: { object?: GeoJSON.Feature }) => {
            const id = info.object?.properties?.segment_id;
            if (id && onSelectSegment) onSelectSegment(String(id));
            return true;
          },
        }),
      ...(drawnRoutes ?? []).map(
        (route) =>
          new GeoJsonLayer({
            id: `drawn-route-${route.id}`,
            data: {
              type: "FeatureCollection",
              features: [
                {
                  type: "Feature",
                  properties: {},
                  geometry: { type: "LineString", coordinates: route.geometry },
                },
              ],
            } as GeoJSON.FeatureCollection,
            stroked: true,
            filled: false,
            getLineColor: route.colour,
            getLineWidth: route.width,
            lineWidthMinPixels: 2,
            lineWidthMaxPixels: 8,
            lineCapRounded: true,
            lineJointRounded: true,
            pickable: false,
          }),
      ),
      toggles.zones &&
        new GeoJsonLayer({
          id: "red-zones",
          data: { type: "FeatureCollection", features: zones } as GeoJSON.FeatureCollection,
          stroked: true,
          filled: true,
          getFillColor: (feature: GeoJSON.Feature) =>
            ZONE_FILL[String(feature.properties?.zone_class)] ?? ZONE_FILL.LOW,
          getLineColor: (feature: GeoJSON.Feature) =>
            ZONE_LINE[String(feature.properties?.zone_class)] ?? ZONE_LINE.LOW,
          getLineWidth: 30,
          lineWidthMinPixels: 0.75,
          lineWidthMaxPixels: 2,
          pickable: true,
          onClick: handleZoneClick,
        }),
      toggles.sites &&
        new ScatterplotLayer({
          id: "sites",
          data: sites,
          getPosition: (site: CandidateSite) => [site.centroid.lon, site.centroid.lat],
          getRadius: 130,
          radiusMinPixels: 5,
          radiusMaxPixels: 12,
          filled: true,
          stroked: true,
          getFillColor: [63, 156, 140, 190],
          getLineColor: [186, 240, 228, 235],
          getLineWidth: 25,
          lineWidthMinPixels: 1.5,
          pickable: true,
        }),
      toggles.habitations &&
        new ScatterplotLayer({
          id: "habitations",
          data: habitations,
          getPosition: (habitation: Habitation) => [
            habitation.centroid.lon,
            habitation.centroid.lat,
          ],
          getRadius: (habitation: Habitation) => 90 + Math.sqrt(habitation.population) * 9,
          radiusMinPixels: 5,
          radiusMaxPixels: 22,
          filled: true,
          stroked: true,
          getFillColor: (habitation: Habitation) =>
            habitationColour?.(habitation) ?? [198, 154, 62, 170],
          getLineColor: (habitation: Habitation) =>
            habitation.id === highlightId ? [231, 238, 247, 255] : [245, 224, 168, 220],
          getLineWidth: (habitation: Habitation) =>
            habitation.id === highlightId ? 60 : 25,
          lineWidthMinPixels: 1.5,
          lineWidthMaxPixels: 4,
          updateTriggers: {
            getFillColor: [habitationColour, highlightId],
            getLineColor: [highlightId],
            getLineWidth: [highlightId],
          },
          pickable: true,
          onClick: (info: { object?: Habitation }) => {
            if (info.object) {
              onSelectPoint(info.object.centroid.lon, info.object.centroid.lat);
            }
            return true;
          },
        }),
      circles &&
        circles.length > 0 &&
        new ScatterplotLayer({
          id: "observation-footprints",
          data: circles,
          getPosition: (circle: MapCircle) => [circle.lon, circle.lat],
          // The radius is in metres because the footprint is a distance on the
          // ground, not a size on the screen: zooming out must shrink it.
          getRadius: (circle: MapCircle) => circle.radius_m,
          radiusUnits: "meters",
          filled: true,
          stroked: true,
          getFillColor: (circle: MapCircle) => withAlpha(circle.colour, 26),
          getLineColor: (circle: MapCircle) => withAlpha(circle.colour, 190),
          getLineWidth: 2,
          lineWidthUnits: "pixels",
          pickable: false,
        }),
      Boolean(toggles.confidence && confidenceUrl) &&
        new BitmapLayer({
          id: "confidence-hatch",
          image: confidenceUrl as string,
          bounds: [min_lon, min_lat, max_lon, max_lat],
          opacity: 1,
          pickable: false,
        }),
      selected &&
        new ScatterplotLayer({
          id: "selection",
          data: [selected],
          getPosition: (point: { lon: number; lat: number }) => [point.lon, point.lat],
          getRadius: 60,
          radiusMinPixels: 8,
          radiusMaxPixels: 14,
          filled: false,
          stroked: true,
          getLineColor: [231, 238, 247, 240],
          getLineWidth: 22,
          lineWidthMinPixels: 2,
        }),
    ].filter(Boolean);

    overlayRef.current.setProps({ layers });
  }, [
    ready,
    network,
    drawnRoutes,
    circles,
    closed,
    closedKey,
    onSelectSegment,
    toggles,
    hazardOpacity,
    confidenceUrl,
    zones,
    habitations,
    sites,
    roads,
    overlayUrl,
    studyArea,
    selected,
    handleZoneClick,
    onSelectPoint,
    habitationColour,
    highlightId,
  ]);

  return (
    <div className="relative h-full w-full">
      <div ref={container} className="h-full w-full" />
      <div className="pointer-events-none absolute bottom-2 left-2 rounded-sm bg-[var(--color-abyss)]/75 px-2 py-1 text-[10px] text-[var(--color-ink-faint)]">
        Terrain: Copernicus DEM GLO-30 &middot; Roads and waterways: OpenStreetMap (ODbL)
        &middot; Zones: ASTRA analytical classification
      </div>
    </div>
  );
}
