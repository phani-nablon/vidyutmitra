import { useState } from 'react';
import { Link } from 'react-router-dom';
import { img } from '../lib/api';
import { Badge, ScoreBar } from './ui';
import { PlanSketch } from './PlanSketch';
import type { Check, SearchResult } from '../lib/types';

const VERDICT: Record<string, { tone: 'blue' | 'amber' | 'red'; text: string }> = {
  reuse: { tone: 'blue', text: 'Reuse as is' },
  adapt: { tone: 'amber', text: 'Adapt' },
  new: { tone: 'red', text: 'Design new' },
};

export function CheckList({ checks }: { checks: Check[] }) {
  if (!checks.length) return null;
  return (
    <ul className="checks">
      {checks.map((c, i) => (
        <li key={`${c.id}-${i}`} className={`check ${c.status}`}>
          <span className="check-dot" aria-hidden />
          <span>{c.detail}</span>
        </li>
      ))}
    </ul>
  );
}

/** One ranked historic design: verdict, score, the original drawing crop and the posed plan side by side. */
export function ResultCard({ rank, result }: { rank: number; result: SearchResult }) {
  const [showAll, setShowAll] = useState(false);
  const v = VERDICT[result.verdict] || { tone: 'amber' as const, text: result.verdict };
  const ov = result.overlay && !result.overlay.error ? result.overlay : undefined;
  const pct = Math.round(result.score * 100);
  const failing = result.checks.filter((c) => c.status === 'fail' || c.status === 'warn');
  const shown = showAll ? result.checks : failing.length ? failing : result.checks.slice(0, 3);
  // equipment layout crop first, the whole page if the crop is not stored, then nothing
  const sources = [ov?.equipment_layout_id && img.layout(result.doc_id, ov.equipment_layout_id), img.page(result.doc_id, ov?.page || 1)].filter(
    Boolean
  ) as string[];
  const [srcIdx, setSrcIdx] = useState(0);
  const imgOk = srcIdx < sources.length;
  const src = sources[srcIdx];

  return (
    <article className="result card glass cbody">
      <header className="result-head">
        <span className="rank">{rank}</span>
        <div className="grow" style={{ minWidth: 0 }}>
          <div className="strong truncate" title={result.file_name}>
            {result.file_name}
          </div>
          <div className="small muted">
            {result.substation_name || 'Substation'} · matched by {result.engine.toUpperCase()}
            {result.status === 'reviewed' ? ' · reviewed drawing' : ''}
          </div>
        </div>
        <Badge tone={v.tone}>{v.text}</Badge>
      </header>

      <div className="result-score">
        <ScoreBar score={pct} />
        <span className="small muted">{Math.round(result.coverage * 100)}% of what you asked could be compared</span>
      </div>

      <div className="result-visuals">
        <figure>
          {imgOk ? (
            <img src={src} alt={`Equipment layout of ${result.file_name}`} onError={() => setSrcIdx((i) => i + 1)} />
          ) : (
            <div className="sketch-empty small muted">No image of this drawing is stored.</div>
          )}
          <figcaption>Original drawing</figcaption>
        </figure>
        <figure>
          {ov ? <PlanSketch overlay={ov} /> : <div className="sketch-empty small muted">No overlay.</div>}
          <figcaption>Historic layout on your site (dashed)</figcaption>
        </figure>
      </div>

      <CheckList checks={shown} />
      {result.checks.length > shown.length && (
        <button className="btn ghost sm" onClick={() => setShowAll(true)}>
          Show all {result.checks.length} checks
        </button>
      )}
      {result.conflicts.length > 0 && (
        <p className="small" style={{ color: 'var(--red-600)' }}>
          Conflicts: {result.conflicts.join('; ')}
        </p>
      )}
      {result.explanation.length > 0 && (
        <details className="small muted">
          <summary>Why it ranked here</summary>
          <ul>
            {result.explanation.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </details>
      )}

      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <Link className="btn sm" to={`/review/${result.doc_id}`}>
          Open the drawing and its fields
        </Link>
      </div>
    </article>
  );
}
