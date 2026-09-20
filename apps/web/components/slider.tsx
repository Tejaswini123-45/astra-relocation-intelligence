"use client";

/**
 * One labelled control that maps to exactly one perturbation value.
 *
 * The note under each slider says which engine the change enters at, because a
 * what-if is only readable if you know how far the thing you moved propagates.
 */
export function Slider({
  label,
  value,
  min,
  max,
  step,
  format,
  onChange,
  note,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (value: number) => string;
  onChange: (value: number) => void;
  note?: string;
}) {
  const id = `slider-${label.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <div className="mt-4">
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={id} className="text-[11px] text-[var(--color-ink)]">
          {label}
        </label>
        <span className="numeric text-[11px] text-[var(--color-ink)]">
          {format(value)}
        </span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="mt-1.5 w-full accent-[var(--color-signal)]"
      />
      {note ? (
        <p className="mt-1 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
          {note}
        </p>
      ) : null}
    </div>
  );
}
