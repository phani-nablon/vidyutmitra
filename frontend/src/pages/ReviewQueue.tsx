import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { Alert, Badge, Card, Empty, PageHead, Spinner } from '../components/ui';
import type { QueueRow } from '../lib/types';

export const STATUS: Record<string, { tone: 'violet' | 'amber' | 'red' | 'blue' | 'default'; text: string }> = {
  reviewed: { tone: 'blue', text: 'Reviewed' },
  provisional: { tone: 'amber', text: 'Provisional' },
  auto: { tone: 'violet', text: 'Auto' },
  awaiting_review: { tone: 'red', text: 'Held for review' },
};

const KIND_LABEL: Record<string, string> = {
  gate: 'Gate',
  unreviewed: 'Unchecked',
  issue: 'Issue',
  missing: 'Missing',
  clearance: 'Clearance',
  flagged: 'Flagged',
};

/** Page 2, step one: every drawing, the ones with the most open actions first. */
export function ReviewQueue() {
  const [rows, setRows] = useState<QueueRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [openOnly, setOpenOnly] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    api
      .queue()
      .then((r) => setRows(r.documents))
      .catch((err) => setError(err.message));
  }, []);

  const shown = useMemo(
    () =>
      (rows || []).filter(
        (r) => (!openOnly || r.open_actions > 0) && (!q || r.file_name.toLowerCase().includes(q.toLowerCase()))
      ),
    [rows, q, openOnly]
  );
  const pending = rows?.filter((r) => r.open_actions > 0).length ?? 0;

  return (
    <>
      <PageHead
        eyebrow="Review drawings · page 2"
        title="Drawings waiting for a human look."
        lead="Sorted by open actions: unchecked extractions, issues the extractor raised, room sizes it could not read, plot clearances under the norm and anything a reviewer flagged."
        chips={rows ? [`${pending} of ${rows.length} need attention`, `${rows.reduce((t, r) => t + r.open_actions, 0)} open actions`] : []}
      />

      {error && <Alert tone="error">{error}</Alert>}
      {!rows && !error && <Spinner label="Loading the drawing list" />}

      {rows && (
        <Card
          title="Drawings"
          caption={`${shown.length} shown`}
          flush
          action={
            <div className="row" style={{ gap: 12 }}>
              <label className="check-opt small">
                <input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} />
                Only with open actions
              </label>
              <input className="input" style={{ width: 220 }} placeholder="Filter by file name" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
          }
        >
          {shown.length === 0 ? (
            <Empty title={openOnly ? 'Nothing left to review' : 'No drawings match'}>
              {openOnly ? 'Every drawing has been checked. Untick the filter to see them all.' : 'Try a different filter.'}
            </Empty>
          ) : (
            <div className="table-scroll">
              <table className="tbl queue">
                <thead>
                  <tr>
                    <th className="num">Open</th>
                    <th>Drawing</th>
                    <th>Status</th>
                    <th>What needs doing</th>
                    <th className="num">Equipment</th>
                    <th className="num">Quality</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r) => {
                    const st = STATUS[r.status || ''] || { tone: 'default' as const, text: r.status || 'unknown' };
                    return (
                      <tr key={r.doc_id} className="clickable" onClick={() => navigate(`/review/${r.doc_id}`)}>
                        <td className="num">
                          <span className={`open-count${r.open_actions ? '' : ' zero'}`}>{r.open_actions}</span>
                        </td>
                        <td style={{ maxWidth: 320 }}>
                          <div className="strong truncate" title={r.file_name}>
                            {r.file_name}
                          </div>
                          <div className="small muted">
                            {[r.installation?.replace(/_/g, ' '), r.floor, `${r.n_substations} substation${r.n_substations === 1 ? '' : 's'}`]
                              .filter(Boolean)
                              .join(' · ')}
                          </div>
                        </td>
                        <td>
                          <Badge tone={st.tone}>{st.text}</Badge>
                        </td>
                        <td>
                          <ul className="action-list">
                            {r.actions.slice(0, 3).map((a, i) => (
                              <li key={i}>
                                <span className={`kind ${a.kind}`}>{KIND_LABEL[a.kind] || a.kind}</span> {a.text}
                              </li>
                            ))}
                            {r.open_actions > 3 && <li className="muted small">and {r.open_actions - 3} more</li>}
                          </ul>
                        </td>
                        <td className="num">{r.n_equipment}</td>
                        <td className="num mono">{r.score != null ? r.score.toFixed(2) : '–'}</td>
                        <td>
                          <Link className="btn sm" to={`/review/${r.doc_id}`} onClick={(e) => e.stopPropagation()}>
                            Review
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </>
  );
}
