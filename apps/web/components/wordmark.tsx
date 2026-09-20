/**
 * The ASTRA mark: a contour motif over a settlement point.
 *
 * Three contour arcs tighten towards a single marked location - the idea the
 * product is built on, that terrain and settlement have to be read together.
 */
export function AstraMark({ size = 28 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      role="img"
      aria-label="ASTRA"
      shapeRendering="geometricPrecision"
    >
      <path
        d="M2.5 24.5C7 18.5 11 15 16 15s9 3.5 13.5 9.5"
        stroke="currentColor"
        strokeOpacity="0.35"
        strokeWidth="1.25"
        strokeLinecap="round"
      />
      <path
        d="M6 25.5C9.5 21 12.5 19 16 19s6.5 2 10 6.5"
        stroke="currentColor"
        strokeOpacity="0.55"
        strokeWidth="1.25"
        strokeLinecap="round"
      />
      <path
        d="M10 26.5c2.4-2.9 4.2-4.2 6-4.2s3.6 1.3 6 4.2"
        stroke="currentColor"
        strokeOpacity="0.8"
        strokeWidth="1.25"
        strokeLinecap="round"
      />
      <path
        d="M16 4.5 20.2 12H11.8L16 4.5Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <circle cx="16" cy="22.3" r="1.7" fill="currentColor" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="text-[var(--color-safe)]">
        <AstraMark />
      </span>
      <span className="flex flex-col leading-none">
        <span className="text-[15px] font-semibold tracking-[0.22em] text-[var(--color-ink)]">
          ASTRA
        </span>
        <span className="mt-1 text-[10px] uppercase tracking-[0.16em] text-[var(--color-ink-faint)]">
          Settlement Risk &amp; Relocation Intelligence
        </span>
      </span>
    </div>
  );
}
