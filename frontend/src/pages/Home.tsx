import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { Badge, PageHead, Section } from '../components/ui';
import type { Health, QueueRow } from '../lib/types';

/** Home: the library at a glance, then the two jobs this tool does. */
export function Home() {
  const { user } = useAuth();
  const [queue, setQueue] = useState<QueueRow[] | null>(null);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    api.queue().then((q) => setQueue(q.documents)).catch(() => setQueue(null));
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  const pending = queue?.filter((r) => r.open_actions > 0) ?? [];
  const reviewed = health?.index?.by_status?.reviewed ?? 0;
  const total = health?.index?.documents ?? 0;
  const actions = queue?.reduce((s, r) => s + r.open_actions, 0) ?? 0;
  const held = queue?.filter((r) => r.status === 'awaiting_review').length ?? 0;
  const first = user?.name.split(' ')[0];

  return (
    <>
      <PageHead
        eyebrow="SLIDe · CSS design retrieval"
        title={`Good to see you${first ? `, ${first}` : ''}.`}
        lead="Start a new substation from the closest past designs, or check what the extractor read off the drawings already in the library."
        chips={[
          `${total} drawings indexed`,
          `${health?.index?.substations ?? 0} substations`,
          `index version ${health?.index?.version ?? 0}`,
          health?.engines?.kg?.ok === false ? 'graph offline · RAG fallback' : 'KG first, RAG fallback',
        ]}
      />

      <div className="vitals v4">
        <div className="vital">
          <div className="vl">Drawings in the library</div>
          <div className="vrow">
            <div className="vn">{total}</div>
          </div>
          <div className="vnote">
            <span className="sdot" style={{ background: 'var(--teal)' }} />
            {health?.index?.substations ?? 0} substations read
          </div>
        </div>
        <div className="vital">
          <div className="vl">Reviewed by a person</div>
          <div className="vrow">
            <div className="vn">
              {reviewed}
              <small> / {total}</small>
            </div>
          </div>
          <div className="vnote">{total ? Math.round((reviewed / total) * 100) : 0}% of the library checked</div>
        </div>
        <div className="vital">
          <div className="vl">Drawings needing review</div>
          <div className="vrow">
            <div className="vn">{queue ? pending.length : '–'}</div>
          </div>
          <div className="vnote">
            <span className="sdot" style={{ background: 'var(--amber)' }} />
            {actions} open actions in total
          </div>
        </div>
        <div className="vital">
          <div className="vl">Held at the review gate</div>
          <div className="vrow">
            <div className="vn">{queue ? held : '–'}</div>
          </div>
          <div className="vnote">not searchable until submitted</div>
        </div>
      </div>

      <Section title="What would you like to do?" sub="two pages, one library" />
      <div className="grid2">
        <Link to="/new" className="card glass jobcard">
          <div className="chead">
            <h3>Create new</h3>
            <span className="tag">Page 1</span>
          </div>
          <div className="cbody">
            <p>
              Describe the new site in your own words, or fill in a short form: where the substation goes, what
              equipment it needs and how big the room is. You get the closest historic designs, with a reuse, adapt or
              new verdict and the checks behind it.
            </p>
            <div className="jobfoot">
              <span className="mono small muted">query builder · text prompt · site plan</span>
              <span className="btn primary">Start a design</span>
            </div>
          </div>
        </Link>

        <Link to="/review" className="card glass jobcard">
          <div className="chead">
            <h3>Review drawings</h3>
            <span className="tag a">Page 2</span>
          </div>
          <div className="cbody">
            <p>
              Work through the drawings that still need a human look, most open actions first. Each one shows the
              original sheet with every extracted field marked on it, so you can correct a value and submit it back to
              the index.
            </p>
            <div className="jobfoot">
              {queue ? (
                <Badge tone={pending.length ? 'amber' : 'blue'}>
                  {pending.length} of {queue.length} need attention
                </Badge>
              ) : (
                <span className="mono small muted">loading the queue</span>
              )}
              <span className="btn">Open the queue</span>
            </div>
          </div>
        </Link>
      </div>

      {pending.length > 0 && (
        <>
          <Section title="Next up for review" sub="most open actions first" right={<Link className="mono small" to="/review">see all →</Link>} />
          <div className="card glass">
            <table className="tbl">
              <tbody>
                {pending.slice(0, 5).map((r) => (
                  <tr key={r.doc_id}>
                    <td style={{ width: 60 }}>
                      <span className="open-count">{r.open_actions}</span>
                    </td>
                    <td>
                      <Link to={`/review/${r.doc_id}`} className="name">
                        {r.file_name}
                      </Link>
                      <div className="small muted">{r.actions[0]?.text}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
