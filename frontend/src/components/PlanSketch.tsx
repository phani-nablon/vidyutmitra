import type { Overlay } from '../lib/types';

type Pt = number[];

const GROUP_COLOUR: Record<string, string> = {
  equipment: 'var(--accent)',
  openings: 'var(--green-600)',
  access: 'var(--green-600)',
  ventilation: 'var(--amber-600)',
  cable: 'var(--brand-2)',
  civil: 'var(--ink-400)',
};

/** Flatten a coords value that may be one ring, a list of rings, or a polyline. */
function rings(coords: unknown): Pt[][] {
  if (!Array.isArray(coords) || coords.length === 0) return [];
  if (typeof coords[0]?.[0] === 'number') return [coords as Pt[]];
  return (coords as unknown[]).flatMap(rings);
}

const path = (pts: Pt[], close = true) =>
  pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(0)},${p[1].toFixed(0)}`).join(' ') + (close ? ' Z' : '');

/**
 * The historic room and its equipment, posed onto the new site outline (dashed) by the alignment step.
 * Coordinates are millimetres in the room frame, y pointing down, which is SVG's own orientation.
 */
export function PlanSketch({ overlay, height = 200 }: { overlay: Overlay; height?: number }) {
  const shapes: Array<{ d: string; group: string; label?: string; c: Pt }> = [];
  const all: Pt[] = [];
  const room = overlay.room ? rings(overlay.room) : [];
  const outline = overlay.query_outline ? rings(overlay.query_outline) : [];
  room.forEach((r) => all.push(...r));
  outline.forEach((r) => all.push(...r));
  for (const it of overlay.items || []) {
    for (const r of rings(it.coords)) {
      if (r.length < 2) continue;
      all.push(...r);
      const c = [r.reduce((s, p) => s + p[0], 0) / r.length, r.reduce((s, p) => s + p[1], 0) / r.length];
      shapes.push({ d: path(r, r.length > 2), group: it.group, label: it.group === 'equipment' ? it.label : undefined, c });
    }
  }
  if (all.length < 2) return <div className="sketch-empty small muted">No room geometry was extracted for this design.</div>;

  const xs = all.map((p) => p[0]);
  const ys = all.map((p) => p[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const pad = Math.max(x1 - x0, y1 - y0) * 0.05 || 100;
  const vb = `${x0 - pad} ${y0 - pad} ${x1 - x0 + 2 * pad} ${y1 - y0 + 2 * pad}`;
  const font = Math.max(x1 - x0, y1 - y0) / 28;

  return (
    <svg className="sketch" viewBox={vb} style={{ height }} preserveAspectRatio="xMidYMid meet">
      {room.map((r, i) => (
        <path key={`r${i}`} d={path(r)} className="sketch-room" vectorEffect="non-scaling-stroke" />
      ))}
      {shapes.map((s, i) => (
        <g key={i}>
          <path
            d={s.d}
            fill={s.group === 'equipment' ? GROUP_COLOUR.equipment : 'none'}
            fillOpacity={0.18}
            stroke={GROUP_COLOUR[s.group] || 'var(--ink-500)'}
            strokeWidth={1.4}
            vectorEffect="non-scaling-stroke"
          />
          {s.label && (
            <text x={s.c[0]} y={s.c[1]} fontSize={font} textAnchor="middle" dominantBaseline="middle" className="sketch-label">
              {s.label}
            </text>
          )}
        </g>
      ))}
      {outline.map((r, i) => (
        <path key={`q${i}`} d={path(r)} className="sketch-site" vectorEffect="non-scaling-stroke" />
      ))}
    </svg>
  );
}
