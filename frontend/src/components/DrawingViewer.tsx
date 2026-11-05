import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { img } from '../lib/api';
import type { Loc, ReviewDoc } from '../lib/types';

export interface Shape {
  id: string;
  loc: Loc;
  tone?: 'item' | 'pass' | 'fail' | 'info';
}

/**
 * The original sheet with every located field drawn over it. Locations come in the pixel frame of the
 * stored page image, so the SVG uses that frame as its viewBox and stretches with the image: whatever
 * size the server scaled the JPEG to, the boxes stay on their items.
 */
export function DrawingViewer({
  doc,
  page,
  setPage,
  shapes,
  selected,
  onSelect,
}: {
  doc: ReviewDoc;
  page: number;
  setPage: (p: number) => void;
  shapes: Shape[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const [zoom, setZoom] = useState(1);
  const [showAll, setShowAll] = useState(true);
  const [broken, setBroken] = useState(false);
  const [tick, setTick] = useState(0);
  const scroller = useRef<HTMLDivElement>(null);
  const meta = doc.pages.find((p) => p.page === page);
  const W = meta?.w || 1000;
  const H = meta?.h || 1000;

  const sel = shapes.find((s) => s.id === selected);

  // Bring the selected item into view: zoom in on small boxes, then centre on it. The centring waits for
  // the layout effect after the zoom change; scrolling in the same tick measures the old, smaller canvas.
  const pending = useRef<number[] | null>(null);
  useEffect(() => {
    if (!sel?.loc.box || sel.loc.page !== page) return;
    const [x0, y0, x1, y1] = sel.loc.box;
    const frac = Math.max((x1 - x0) / W, (y1 - y0) / H);
    pending.current = sel.loc.box;
    setZoom((cur) => Math.max(cur, frac < 0.08 ? 3 : frac < 0.2 ? 2 : 1));
    setTick((t) => t + 1);
  }, [selected, page]); // eslint-disable-line react-hooks/exhaustive-deps

  useLayoutEffect(() => {
    const el = scroller.current;
    const box = pending.current;
    if (!el || !box) return;
    pending.current = null;
    const cx = ((box[0] + box[2]) / 2 / W) * el.scrollWidth;
    const cy = ((box[1] + box[3]) / 2 / H) * el.scrollHeight;
    el.scrollTo({ left: cx - el.clientWidth / 2, top: cy - el.clientHeight / 2, behavior: 'smooth' });
  }, [zoom, tick, W, H]);

  useEffect(() => setBroken(false), [page, doc.doc_id]);

  const onPage = shapes
    .filter((s) => s.loc.page === page && (s.loc.box || s.loc.polygon))
    .sort((a, b) => Number(a.id === selected) - Number(b.id === selected)); // selected drawn last, on top
  const stroke = Math.max(W, H) / 700;

  return (
    <div className="dv">
      <div className="dv-bar">
        {doc.pages.length > 1 && (
          <select className="select" value={page} onChange={(e) => setPage(Number(e.target.value))}>
            {doc.pages.map((p) => (
              <option key={p.page} value={p.page}>
                Page {p.page}
              </option>
            ))}
          </select>
        )}
        <button className="btn sm" onClick={() => setZoom((z) => Math.max(1, z / 1.5))}>
          −
        </button>
        <span className="mono small">{Math.round(zoom * 100)}%</span>
        <button className="btn sm" onClick={() => setZoom((z) => Math.min(8, z * 1.5))}>
          +
        </button>
        <button className="btn ghost sm" onClick={() => setZoom(1)}>
          Fit
        </button>
        <label className="check-opt small">
          <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
          Show every field
        </label>
        <div className="grow" />
        {sel && sel.loc.precision !== 'exact' && (
          <span className="small muted" title={sel.loc.why || ''}>
            {sel.loc.precision === 'none' ? 'No position recorded' : sel.loc.why}
          </span>
        )}
      </div>

      <div className="dv-scroll" ref={scroller}>
        {meta?.has_image === false || broken ? (
          <div className="sketch-empty small muted">No page image is stored for this drawing.</div>
        ) : (
          <div className="dv-canvas" style={{ width: `${zoom * 100}%`, aspectRatio: `${W} / ${H}` }}>
            <img src={img.page(doc.doc_id, page)} alt={`Page ${page} of ${doc.file_name}`} onError={() => setBroken(true)} />
            <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
              {onPage.map((s) => {
                const on = s.id === selected;
                if (!on && !showAll) return null;
                const cls = `dv-shape ${s.tone || 'item'}${on ? ' on' : ''} ${s.loc.precision}`;
                const common = { className: cls, strokeWidth: on ? stroke * 2.2 : stroke, onClick: () => onSelect(s.id) };
                return s.loc.polygon && s.loc.polygon.length > 2 ? (
                  <polygon key={s.id} points={s.loc.polygon.map((p) => p.join(',')).join(' ')} {...common} />
                ) : s.loc.box ? (
                  <rect
                    key={s.id}
                    x={s.loc.box[0]}
                    y={s.loc.box[1]}
                    width={s.loc.box[2] - s.loc.box[0]}
                    height={s.loc.box[3] - s.loc.box[1]}
                    {...common}
                  />
                ) : null;
              })}
            </svg>
          </div>
        )}
      </div>
    </div>
  );
}
