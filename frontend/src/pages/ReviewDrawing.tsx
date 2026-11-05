import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { Alert, Badge, PageHead, Spinner } from '../components/ui';
import { DrawingViewer, type Shape } from '../components/DrawingViewer';
import { STATUS } from './ReviewQueue';
import type { Edit, Mark, ReviewDoc, ReviewItem, ReviewValue } from '../lib/types';

type Draft = Record<string, string | number | boolean | null>; // `${item_id}|${field}` -> value
const key = (item: string, field: string) => `${item}|${field}`;
const pretty = (name: string) =>
  name
    .replace(/_mm$/, ' (mm)')
    .replace(/_m2$/, ' (m²)')
    .replace(/_kg$/, ' (kg)')
    .replace(/_m$/, ' (m)')
    .replace(/^kva$/, 'kVA')
    .replace(/^L /, 'Length ')
    .replace(/_/g, ' ')
    .replace(/^./, (c) => c.toUpperCase());

function ValueInput({
  field,
  value,
  onChange,
}: {
  field: { name: string; type: string };
  value: string | number | boolean | null | undefined;
  onChange: (v: string | number | boolean | null) => void;
}) {
  if (field.type === 'boolean') {
    return (
      <select className="select" value={value === true ? 'yes' : value === false ? 'no' : ''} onChange={(e) => onChange(e.target.value === '' ? null : e.target.value === 'yes')}>
        <option value="">–</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    );
  }
  const numeric = field.type === 'number' || field.type === 'integer';
  return (
    <input
      className="input"
      type={numeric ? 'number' : 'text'}
      step={field.type === 'integer' ? 1 : 'any'}
      value={value === null || value === undefined ? '' : String(value)}
      onChange={(e) => onChange(e.target.value === '' ? null : numeric ? Number(e.target.value) : e.target.value)}
    />
  );
}

function MarkButtons({ mark, onMark }: { mark?: Mark | null; onMark: (v: Mark['verdict'], note?: string) => void }) {
  const [note, setNote] = useState(mark?.note || '');
  useEffect(() => setNote(mark?.note || ''), [mark?.note]);
  return (
    <div className="mark">
      <span className="small muted">Is this right?</span>
      {(['correct', 'wrong', 'unsure'] as const).map((v) => (
        <button key={v} className={`mark-btn ${v}${mark?.verdict === v ? ' on' : ''}`} onClick={() => onMark(mark?.verdict === v ? null : v, note)}>
          {v === 'correct' ? 'Correct' : v === 'wrong' ? 'Wrong' : 'Not sure'}
        </button>
      ))}
      <input
        className="input"
        placeholder="Note for the next reviewer"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        onBlur={() => note !== (mark?.note || '') && onMark(mark?.verdict ?? null, note)}
      />
    </div>
  );
}

/** Page 2, step two: one drawing, its fields beside the sheet they were read from. Edit, then submit. */
export function ReviewDrawing() {
  const { docId = '' } = useParams();
  const { can } = useAuth();
  const canEdit = can('edit');
  const navigate = useNavigate();
  const [doc, setDoc] = useState<ReviewDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [tab, setTab] = useState<'fields' | 'clearances'>('fields');
  const [draft, setDraft] = useState<Draft>({});
  const [adding, setAdding] = useState<Record<string, string[]>>({});
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const load = useCallback(() => {
    return api
      .review(docId)
      .then((d) => {
        setDoc(d);
        setOpen((o) => (Object.keys(o).length ? o : Object.fromEntries(d.groups.map((g, i) => [g.id, i < 2]))));
      })
      .catch((err) => setError(err.message));
  }, [docId]);

  useEffect(() => {
    setDoc(null);
    setDraft({});
    setSelected(null);
    load();
  }, [load]);

  const items = useMemo(() => (doc ? doc.groups.flatMap((g) => g.items) : []), [doc]);
  const shapes: Shape[] = useMemo(() => {
    if (!doc) return [];
    if (tab === 'clearances') return doc.clearances.map((c) => ({ id: c.id, loc: c.loc, tone: c.status }));
    return items.map((i) => ({ id: i.id, loc: i.loc, tone: 'item' as const }));
  }, [doc, items, tab]);

  const select = (id: string) => {
    setSelected(id);
    const it = items.find((i) => i.id === id) || doc?.clearances.find((c) => c.id === id);
    if (it?.loc?.page) setPage(it.loc.page);
    const g = doc?.groups.find((gr) => gr.items.some((i) => i.id === id));
    if (g) setOpen((o) => ({ ...o, [g.id]: true }));
    requestAnimationFrame(() => document.getElementById(`item-${id}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
  };

  const edits: Edit[] = Object.entries(draft).map(([k, value]) => {
    const [item_id, name] = [k.slice(0, k.lastIndexOf('|')), k.slice(k.lastIndexOf('|') + 1)];
    return { item_id, name, value };
  });

  async function onMark(item: ReviewItem, verdict: Mark['verdict'], note = '') {
    if (!doc) return;
    try {
      const marks = await api.mark(doc.doc_id, item.id, verdict, note);
      setDoc({
        ...doc,
        marks,
        groups: doc.groups.map((g) => ({ ...g, items: g.items.map((i) => (i.id === item.id ? { ...i, mark: marks[item.id] ?? null } : i)) })),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the mark');
    }
  }

  async function save(submit: boolean) {
    if (!doc) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (submit) {
        const r = await api.submit(doc.doc_id, edits);
        setNotice(`Submitted. ${r.changed.length} field${r.changed.length === 1 ? '' : 's'} changed, index version ${r.version} published.`);
      } else {
        const r = await api.saveEdits(doc.doc_id, edits);
        setNotice(`Saved ${r.changed.length} change${r.changed.length === 1 ? '' : 's'}. Submit when the drawing is done.`);
      }
      setDraft({});
      setAdding({});
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
      if (submit) await load(); // edits are saved even when indexing fails
    } finally {
      setBusy(false);
    }
  }

  if (error && !doc) return <Alert tone="error">{error}</Alert>;
  if (!doc) return <Spinner label="Loading the drawing" />;

  const st = STATUS[doc.status || ''] || { tone: 'default' as const, text: doc.status || 'unknown' };
  const dirty = edits.length;
  const fails = doc.clearances.filter((c) => c.status === 'fail').length;

  return (
    <div className="wide">
      <PageHead
        eyebrow="Review drawings · one drawing"
        title={doc.file_name}
        chips={[
          <Badge key="s" tone={st.tone}>{st.text}</Badge>,
          doc.score != null ? `quality ${doc.score.toFixed(2)}` : 'quality –',
          `${items.length} fields read`,
          `${doc.actions.length} open action${doc.actions.length === 1 ? '' : 's'}`,
          fails ? `${fails} failing clearance${fails === 1 ? '' : 's'}` : 'no failing clearances',
        ]}
        actions={
          canEdit && (
            <>
              <button className="btn" disabled={busy || !dirty} onClick={() => save(false)}>
                Save draft{dirty ? ` (${dirty})` : ''}
              </button>
              <button className="btn primary" disabled={busy} onClick={() => save(true)} title="Save, mark the drawing reviewed and re-index it">
                {busy ? 'Working…' : 'Submit review'}
              </button>
            </>
          )
        }
      />

      {!canEdit && <Alert tone="info">You are signed in as a viewer: you can look through the fields, but only an editor can change or submit them.</Alert>}
      {error && <Alert tone="error">{error}</Alert>}
      {notice && (
        <Alert tone="success">
          {notice} <Link to="/review">Back to the list</Link> or{' '}
          <button className="linklike" onClick={() => navigate('/review')}>take the next drawing</button>.
        </Alert>
      )}

      <div className="review-split">
        <div className="review-left">
          <DrawingViewer doc={doc} page={page} setPage={setPage} shapes={shapes} selected={selected} onSelect={select} />
        </div>

        <div className="review-right">
          {doc.actions.length > 0 && (
            <div className="card glass cbody actions-card">
              <h3>Open actions</h3>
              <ul className="action-list">
                {doc.actions.map((a, i) => (
                  <li key={i}>
                    <span className={`kind ${a.kind}`}>{a.kind}</span>{' '}
                    {a.item_id ? <button className="linklike" onClick={() => select(a.item_id!)}>{a.text}</button> : a.text}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="tabs" style={{ margin: 0 }}>
            <button className={`tab${tab === 'fields' ? ' on' : ''}`} onClick={() => setTab('fields')}>
              Fields ({items.length})
            </button>
            <button className={`tab${tab === 'clearances' ? ' on' : ''}`} onClick={() => setTab('clearances')}>
              Clearances ({doc.clearances.length}{fails ? `, ${fails} failing` : ''})
            </button>
          </div>

          {tab === 'fields' &&
            doc.groups.map((g) => (
              <div key={g.id} className="card glass group">
                <button className="group-head" onClick={() => setOpen((o) => ({ ...o, [g.id]: !o[g.id] }))}>
                  <span className="strong">{g.label}</span>
                  <span className="muted small">
                    {g.items.filter((i) => i.mark?.verdict === 'correct').length}/{g.items.length} confirmed {open[g.id] ? '▾' : '▸'}
                  </span>
                </button>
                {open[g.id] &&
                  g.items.map((it) => {
                    const on = it.id === selected;
                    const extra = adding[it.id] || [];
                    const changed = Object.keys(draft).filter((k) => k.startsWith(`${it.id}|`)).length;
                    return (
                      <div key={it.id} id={`item-${it.id}`} className={`item${on ? ' on' : ''}`}>
                        <button className="item-head" onClick={() => select(it.id)}>
                          <span className="truncate">
                            <span className="strong">{it.label}</span>
                            {it.class_label && <span className="muted small"> · {it.class_label}</span>}
                          </span>
                          <span className="row" style={{ gap: 6 }}>
                            {changed > 0 && <Badge tone="blue">{changed} edited</Badge>}
                            {it.mark?.verdict && (
                              <Badge tone={it.mark.verdict === 'correct' ? 'blue' : it.mark.verdict === 'wrong' ? 'red' : 'amber'}>{it.mark.verdict}</Badge>
                            )}
                            {it.loc.precision === 'none' && <span className="small muted" title="No position on the drawing">no position</span>}
                          </span>
                        </button>
                        {on && (
                          <fieldset className="item-body" disabled={!canEdit}>
                            <table className="fields">
                              <tbody>
                                {it.values.filter((v) => v.editable).map((v: ReviewValue) => {
                                  const k = key(it.id, v.name);
                                  return (
                                    <tr key={v.name} className={k in draft ? 'dirty' : ''}>
                                      <th title={v.help}>{pretty(v.name)}</th>
                                      <td>
                                        <ValueInput field={v} value={k in draft ? draft[k] : v.value} onChange={(x) => setDraft((d) => ({ ...d, [k]: x }))} />
                                        {v.note && <div className="small muted">{v.note}</div>}
                                      </td>
                                    </tr>
                                  );
                                })}
                                {extra.map((name) => {
                                  const m = it.missing.find((x) => x.name === name)!;
                                  const k = key(it.id, name);
                                  return (
                                    <tr key={name} className="dirty">
                                      <th title={m.help}>{pretty(name)}</th>
                                      <td>
                                        <ValueInput field={m} value={draft[k]} onChange={(x) => setDraft((d) => ({ ...d, [k]: x }))} />
                                      </td>
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                            {it.values.some((v) => !v.editable) && (
                              <details className="small">
                                <summary className="muted">Technical details (read only)</summary>
                                <table className="fields">
                                  <tbody>
                                    {it.values
                                      .filter((v) => !v.editable)
                                      .map((v) => (
                                        <tr key={v.name}>
                                          <th title={v.help}>{pretty(v.name)}</th>
                                          <td className="mono small">{v.summary}</td>
                                        </tr>
                                      ))}
                                  </tbody>
                                </table>
                              </details>
                            )}
                            {it.missing.filter((m) => !extra.includes(m.name)).length > 0 && (
                              <select
                                className="select add-field"
                                value=""
                                onChange={(e) => e.target.value && setAdding((a) => ({ ...a, [it.id]: [...extra, e.target.value] }))}
                              >
                                <option value="">Add a value the extractor missed…</option>
                                {it.missing
                                  .filter((m) => !extra.includes(m.name))
                                  .map((m) => (
                                    <option key={m.name} value={m.name}>
                                      {pretty(m.name)}
                                    </option>
                                  ))}
                              </select>
                            )}
                            <MarkButtons mark={it.mark} onMark={(v, note) => onMark(it, v, note)} />
                          </fieldset>
                        )}
                      </div>
                    );
                  })}
              </div>
            ))}

          {tab === 'clearances' && (
            <div className="card glass">
              {doc.clearances.length === 0 && <p className="small muted" style={{ padding: 16 }}>No clearances were measured on this drawing.</p>}
              {doc.clearances.map((c) => (
                <button key={c.id} className={`clr ${c.status}${c.id === selected ? ' on' : ''}`} onClick={() => select(c.id)}>
                  <span className="check-dot" aria-hidden />
                  <span className="grow">
                    <span className="strong">{c.label}</span>
                    <span className="small muted"> · {c.values.map((v) => `${pretty(v.name)} ${v.summary}`).join(' · ')}</span>
                  </span>
                </button>
              ))}
            </div>
          )}

          {doc.edits.length > 0 && (
            <details className="card glass cbody small">
              <summary className="strong">Edit history ({doc.edits.length})</summary>
              <ul className="muted">
                {doc.edits
                  .slice()
                  .reverse()
                  .map((e, i) => (
                    <li key={i}>
                      {e.at.slice(0, 16).replace('T', ' ')} · {e.item_id} · {pretty(e.name)}: {String(e.old ?? '–')} → {String(e.new ?? '–')}
                    </li>
                  ))}
              </ul>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
