import type { CandidateSite, Habitation } from "@astra/contracts";

/**
 * The study corridor: shaded relief rendered from the vendored Copernicus DEM by
 * the API, with habitations and candidate sites placed at their real coordinates.
 *
 * The only arithmetic here is the geographic-to-pixel projection needed to draw
 * a point in the right place. Every value shown as text comes from the API.
 */
export function TerrainPlate({
  imageUrl,
  bbox,
  habitations,
  sites,
}: {
  imageUrl: string;
  bbox: number[];
  habitations: Habitation[];
  sites: CandidateSite[];
}) {
  const [minLon, minLat, maxLon, maxLat] = bbox;
  const width = 1000;
  const height = Math.round((width * (maxLat - minLat)) / (maxLon - minLon));

  const project = (lon: number, lat: number) => ({
    x: ((lon - minLon) / (maxLon - minLon)) * width,
    y: ((maxLat - lat) / (maxLat - minLat)) * height,
  });

  return (
    <figure className="m-0">
      <div className="relative overflow-hidden rounded border border-[var(--color-line)] bg-[var(--color-surface-inset)]">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="block h-auto w-full"
          role="img"
          aria-label="Study corridor terrain with habitations and candidate relocation sites"
        >
          <image
            href={imageUrl}
            x={0}
            y={0}
            width={width}
            height={height}
            preserveAspectRatio="none"
          />

          {sites.map((site) => {
            const { x, y } = project(site.centroid.lon, site.centroid.lat);
            return (
              <g key={site.id}>
                <title>{`${site.id} ${site.name} - candidate relocation site`}</title>
                <rect
                  x={x - 6}
                  y={y - 6}
                  width={12}
                  height={12}
                  fill="none"
                  stroke="var(--color-safe)"
                  strokeWidth={2}
                  transform={`rotate(45 ${x} ${y})`}
                />
                <text
                  x={x + 11}
                  y={y + 4}
                  fill="var(--color-safe)"
                  fontSize={11}
                  fontFamily="var(--font-mono)"
                >
                  {site.id}
                </text>
              </g>
            );
          })}

          {habitations.map((habitation) => {
            const { x, y } = project(habitation.centroid.lon, habitation.centroid.lat);
            const radius = 4 + Math.sqrt(habitation.population) / 6;
            return (
              <g key={habitation.id}>
                <title>
                  {`${habitation.id} ${habitation.name} - ${habitation.population} residents`}
                </title>
                <circle
                  cx={x}
                  cy={y}
                  r={radius}
                  fill="color-mix(in srgb, var(--color-warning) 45%, transparent)"
                  stroke="var(--color-warning)"
                  strokeWidth={1.5}
                />
                <text
                  x={x + radius + 4}
                  y={y + 4}
                  fill="var(--color-ink)"
                  fontSize={11}
                  fontFamily="var(--font-mono)"
                >
                  {habitation.id}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <figcaption className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px] text-[var(--color-ink-faint)]">
        <span className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-block h-2.5 w-2.5 rounded-full"
            style={{
              background: "color-mix(in srgb, var(--color-warning) 45%, transparent)",
              border: "1.5px solid var(--color-warning)",
            }}
          />
          Habitation, sized by population
        </span>
        <span className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-block h-2.5 w-2.5 rotate-45"
            style={{ border: "2px solid var(--color-safe)" }}
          />
          Candidate relocation site
        </span>
        <span className="numeric">
          Shaded relief computed from the Copernicus DEM GLO-30 clip &middot; bbox{" "}
          {bbox.map((value) => value.toFixed(2)).join(", ")}
        </span>
      </figcaption>
    </figure>
  );
}
