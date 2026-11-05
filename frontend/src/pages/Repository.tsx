import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, repoFile } from '../lib/api';
import { Alert, Badge, Card, Empty, Field, PageHead, Section, Spinner } from '../components/ui';
import type { RepoCategory, RepoDivision, RepoDrawing, RepoParams, RepoProject, RepoStage } from '../lib/types';

/*
 * Classic repository browse, following the detailed design's hierarchy:
 *   Category (CSS / DSS) -> Division -> Project (ZLDP, or developer when none is allocated) -> Stage -> Revision.
 * Data comes from the VidyutMitra SubstationRepository (read only); api/repository.py explains the link.
 */

const STAGE_TONE: Record<string, 'default' | 'blue' | 'violet' | 'amber'> = { inputs: 'default', tentative: 'violet', detailed: 'blue', pwd: 'amber' };
const when = (s?: string | null) => (s ? new Date(s).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '–');
const kb = (n?: number) => (n == null ? '' : n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
const projectName = (p: RepoProject) => p.zldp || p.developer;

function Crumbs({ items }: { items: Array<{ label: string; to?: string }> }) {
  return (
    <nav className="repo-crumbs" aria-label="Repository path">
      {items.map((it, i) => (
        <span key={i}>
          {i > 0 && <span className="sep">/</span>}
          {it.to ? <Link to={it.to}>{it.label}</Link> : <b>{it.label}</b>}
        </span>
      ))}
    </nav>
  );
}

function useStatus() {
  const [status, setStatus] = useState<{ configured: boolean; path: string; message?: string } | null>(null);
  useEffect(() => {
    api.repoStatus().then(setStatus).catch((e) => setStatus({ configured: false, path: '', message: e.message }));
  }, []);
  return status;
}

function NotConfigured({ path, message }: { path: string; message?: string }) {
  return (
    <Alert tone="warn">
      {message || 'No VidyutMitra repository was found.'} Looked in <span className="mono">{path || '(not set)'}</span>. Set{' '}
      <span className="mono">repository.path</span> in config/settings.yaml (or <span className="mono">SR_REPO_PATH</span>) to the
      SubstationRepository folder and restart <span className="mono">sr serve</span>.
    </Alert>
  );
}

/** Stage chips: one per folder, with the newest revision number. */
function StageChips({ stages }: { stages: Record<string, number> }) {
  return (
    <span className="stagechips">
      {(['inputs', 'tentative', 'detailed', 'pwd'] as const).map((s) => (
        <span key={s} className={`stagechip${stages[s] ? ' on' : ''}`} title={stages[s] ? `${s}: v${stages[s]}` : `${s}: empty`}>
          {s === 'inputs' ? 'INP' : s === 'tentative' ? 'TEN' : s === 'detailed' ? 'DET' : 'PWD'}
          {stages[s] ? ` v${String(stages[s]).padStart(2, '0')}` : ''}
        </span>
      ))}
    </span>
  );
}

// ------------------------------------------------------------------ level 1: category + find a drawing
export function RepoHome() {
  const status = useStatus();
  const [cats, setCats] = useState<RepoCategory[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [f, setF] = useState({ q: '', category: '', stage: '', min_area: '', max_area: '', equipment: '' });
  const [hits, setHits] = useState<RepoDrawing[] | null>(null);

  useEffect(() => {
    if (status?.configured) api.repoCategories().then(setCats).catch((e) => setError(e.message));
  }, [status?.configured]);

  useEffect(() => {
    if (!status?.configured) return;
    const t = setTimeout(() => api.repoSearch(f).then(setHits).catch((e) => setError(e.message)), 200);
    return () => clearTimeout(t);
  }, [f, status?.configured]);

  const set = (k: keyof typeof f, v: string) => setF((x) => ({ ...x, [k]: v }));

  return (
    <>
      <PageHead
        eyebrow="Repository · classic browse"
        title="Find a drawing where it is filed."
        lead="Category, then division, then project; every project holds the same four stage folders and keeps every revision. Or search below on the parameters the similarity search compares."
        chips={['Category → Division → Project → Stage → Revision', 'read from the VidyutMitra repository']}
      />
      {status && !status.configured && <NotConfigured path={status.path} message={status.message} />}
      {error && <Alert tone="error">{error}</Alert>}
      {status?.configured && !cats && !error && <Spinner />}

      {cats && (
        <>
          <Section title="Category" sub="level 1" />
          <div className="grid2">
            {cats.map((c) => (
              <Link key={c.key} to={`/repo/${c.key}`} className="card glass jobcard">
                <div className="chead">
                  <h3>
                    {c.label} <span className="muted small">· {c.long}</span>
                  </h3>
                  <span className={`tag${c.key === 'dss' ? ' v' : ''}`}>{c.label}</span>
                </div>
                <div className="cbody">
                  <p>{c.key === 'css' ? 'Consumer substations, 20–200 m², open to sky or part of a building.' : 'Distribution substations, 200–800 m², on their own plot.'}</p>
                  <div className="jobfoot">
                    <span className="mono small muted">
                      {c.divisions} divisions · {c.projects} projects · {c.drawings} drawings
                    </span>
                    <span className="btn">Browse</span>
                  </div>
                </div>
              </Link>
            ))}
          </div>

          <Section title="Find a drawing" sub="B6 · same parameters the similarity search uses" />
          <Card title="Filters" caption={hits ? `${hits.length} drawings` : ''}>
            <div className="filters">
              <Field label="Search">
                <input className="input" value={f.q} onChange={(e) => set('q', e.target.value)} placeholder="ZLDP, developer, file name" />
              </Field>
              <Field label="Category">
                <select className="select" value={f.category} onChange={(e) => set('category', e.target.value)}>
                  <option value="">Any</option>
                  <option value="css">CSS</option>
                  <option value="dss">DSS</option>
                </select>
              </Field>
              <Field label="Stage">
                <select className="select" value={f.stage} onChange={(e) => set('stage', e.target.value)}>
                  <option value="">Any</option>
                  <option value="inputs">Inputs</option>
                  <option value="tentative">Tentative</option>
                  <option value="detailed">Detailed</option>
                  <option value="pwd">PWD</option>
                </select>
              </Field>
              <Field label="Room area from (m²)">
                <input className="input" type="number" value={f.min_area} onChange={(e) => set('min_area', e.target.value)} />
              </Field>
              <Field label="to (m²)">
                <input className="input" type="number" value={f.max_area} onChange={(e) => set('max_area', e.target.value)} />
              </Field>
              <Field label="Equipment">
                <select className="select" value={f.equipment} onChange={(e) => set('equipment', e.target.value)}>
                  <option value="">Any</option>
                  <option value="DT">Transformer</option>
                  <option value="RMU">RMU</option>
                  <option value="LTP">LT panel</option>
                  <option value="ICOG">ICOG</option>
                </select>
              </Field>
            </div>
          </Card>
          {hits && <DrawingTable rows={hits} showProject />}
        </>
      )}
    </>
  );
}

function DrawingTable({ rows, showProject }: { rows: RepoDrawing[]; showProject?: boolean }) {
  if (!rows.length) return <Empty title="No drawings match">Try fewer filters.</Empty>;
  return (
    <div className="card glass table-scroll">
      <table className="tbl">
        <thead>
          <tr>
            <th>Drawing</th>
            {showProject && <th>Project</th>}
            <th>Stage</th>
            <th className="num">Room m²</th>
            <th>Uploaded</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 100).map((d) => (
            <tr key={d.id}>
              <td style={{ maxWidth: 360 }}>
                <div className="name truncate" title={d.filename}>{d.filename}</div>
                <div className="small muted truncate" title={d.original_name}>{d.original_name}</div>
              </td>
              {showProject && d.project && (
                <td>
                  <Link to={`/repo/p/${d.project.id}`} className="name">
                    {projectName(d.project)}
                  </Link>
                  <div className="small muted">{d.project.division}</div>
                </td>
              )}
              <td>
                <Badge tone={STAGE_TONE[d.stage]}>
                  {d.stage} v{String(d.version).padStart(2, '0')}
                </Badge>
              </td>
              <td className="num mono">{d.params?.area_sqm ?? '–'}</td>
              <td className="small">{when(d.uploaded_at)}</td>
              <td>
                <FileActions d={d} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FileActions({ d }: { d: RepoDrawing }) {
  return (
    <span className="row" style={{ gap: 6, justifyContent: 'flex-end' }}>
      {d.doc_id && (
        <Link className="btn sm" to={`/review/${d.doc_id}`} title="This file is in the retrieval library: open its extracted fields">
          Fields
        </Link>
      )}
      {d.on_disk ? (
        <>
          <a className="btn sm" href={repoFile(d.id)} target="_blank" rel="noreferrer">
            View
          </a>
          <a className="btn sm ghost" href={repoFile(d.id, true)}>
            Download
          </a>
        </>
      ) : (
        <span className="small muted" title="Listed in the metadata, but the file is not in the folder (often a cloud-only file on the share)">
          not on disk
        </span>
      )}
    </span>
  );
}

// ------------------------------------------------------------------ level 2: divisions of a category
export function RepoDivisions() {
  const { category = 'css' } = useParams();
  const [data, setData] = useState<{ category: RepoCategory; divisions: RepoDivision[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setData(null);
    api.repoDivisions(category).then(setData).catch((e) => setError(e.message));
  }, [category]);
  return (
    <>
      <Crumbs items={[{ label: 'Repository', to: '/repo' }, { label: category.toUpperCase() }]} />
      <PageHead
        eyebrow={`Repository · ${category.toUpperCase()} · level 2`}
        title="Pick a division."
        lead="Divisions come from the controlled master list; each holds the projects filed under it."
        chips={data ? [`${data.divisions.length} divisions`, `${data.divisions.reduce((t, d) => t + d.projects, 0)} projects`] : []}
      />
      {error && <Alert tone="error">{error}</Alert>}
      {!data && !error && <Spinner />}
      {data && (
        <div className="divgrid">
          {data.divisions.map((d) => (
            <Link key={d.slug} to={`/repo/${category}/${d.slug}`} className="card glass divcard">
              <div className="cbody">
                <h3>{d.name}</h3>
                <div className="mono small muted">
                  {d.projects} projects · {d.drawings} drawings
                </div>
                {d.unassigned > 0 && <Badge tone="amber">{d.unassigned} without a ZLDP number</Badge>}
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}

// ------------------------------------------------------------------ level 3: projects of a division
export function RepoProjects() {
  const { category = 'css', division = '' } = useParams();
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [data, setData] = useState<{ division: { slug: string; name: string }; projects: RepoProject[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const t = setTimeout(() => api.repoProjects(category, division, q).then(setData).catch((e) => setError(e.message)), 180);
    return () => clearTimeout(t);
  }, [category, division, q]);
  const name = data?.division.name || division;
  return (
    <>
      <Crumbs items={[{ label: 'Repository', to: '/repo' }, { label: category.toUpperCase(), to: `/repo/${category}` }, { label: name }]} />
      <PageHead
        eyebrow={`Repository · ${category.toUpperCase()} · ${name} · level 3`}
        title="Pick a project."
        lead="Projects are filed by ZLDP number, or by developer where no number is allocated. The chips show the newest revision in each stage folder."
        chips={data ? [`${data.projects.length} projects`, `${data.projects.filter((p) => !p.zldp).length} without a ZLDP number`] : []}
      />
      {error && <Alert tone="error">{error}</Alert>}
      <Card
        title="Projects"
        flush
        action={<input className="input" style={{ width: 240 }} placeholder="ZLDP, developer or site" value={q} onChange={(e) => setQ(e.target.value)} />}
      >
        {!data ? (
          <Spinner />
        ) : data.projects.length === 0 ? (
          <Empty title="No projects here">{q ? 'Nothing matches that search.' : 'This division has no projects yet.'}</Empty>
        ) : (
          <div className="table-scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Project</th>
                  <th>Developer · site</th>
                  <th>Stage folders</th>
                  <th className="num">Files</th>
                  <th>Last activity</th>
                </tr>
              </thead>
              <tbody>
                {data.projects.map((p) => (
                  <tr key={p.id} className="clickable" onClick={() => navigate(`/repo/p/${p.id}`)}>
                    <td>
                      <Link to={`/repo/p/${p.id}`} className="name mono" onClick={(e) => e.stopPropagation()}>
                        {p.zldp || 'no number'}
                      </Link>
                      {!p.zldp && (
                        <div>
                          <Badge tone="amber">unassigned</Badge>
                        </div>
                      )}
                    </td>
                    <td style={{ maxWidth: 360 }}>
                      <div className="name">{p.developer}</div>
                      {p.site && <div className="small muted truncate">{p.site}</div>}
                    </td>
                    <td>
                      <StageChips stages={p.stages} />
                    </td>
                    <td className="num">{p.drawings}</td>
                    <td className="small">{when(p.last_activity)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

/** Repository parameters -> the Create new design-inputs form. */
export function inputsFromParams(p: RepoParams, category: string) {
  const placement = (p.placement || '').toLowerCase();
  const floor = (p.floor || '').toLowerCase();
  const lines = p.equipment.map((e, i) => `${i + 1}. ${e.label}${e.rating && !e.label.includes(e.rating) ? ` ${e.rating}` : ''} – ${e.quantity ?? 1} nos`);
  return {
    category,
    siteType: placement.includes('open') ? 'open_to_sky' : 'in_building',
    openKind: (p.placement_sub || '').includes('rg') ? 'rg' : 'open_plot',
    floor: floor.includes('basement') ? 'basement' : floor.includes('stilt') ? 'stilt' : floor.includes('ground') ? 'ground' : '',
    clearHeight: p.clear_height_mm ? String(p.clear_height_mm / 1000) : '',
    request: lines.join('\n'),
    length: p.length_m ? String(p.length_m) : '',
    width: p.width_m ? String(p.width_m) : '',
  };
}

// ------------------------------------------------------------------ level 4–5: project record, stages and revisions
export function RepoProjectPage() {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<{ project: RepoProject; stages: RepoStage[]; parameters: RepoParams | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stage, setStage] = useState('detailed');
  useEffect(() => {
    api
      .repoProject(id)
      .then((d) => {
        setData(d);
        const first = d.stages.find((s) => s.key === 'detailed' && s.revisions.length) || d.stages.find((s) => s.revisions.length);
        if (first) setStage(first.key);
      })
      .catch((e) => setError(e.message));
  }, [id]);
  const st = useMemo(() => data?.stages.find((s) => s.key === stage), [data, stage]);

  if (error) return <Alert tone="error">{error}</Alert>;
  if (!data) return <Spinner label="Loading the project" />;
  const p = data.project;
  const prm = data.parameters;

  return (
    <>
      <Crumbs
        items={[
          { label: 'Repository', to: '/repo' },
          { label: p.category.toUpperCase(), to: `/repo/${p.category}` },
          { label: p.division_name || p.division, to: `/repo/${p.category}/${p.division}` },
          { label: projectName(p) },
        ]}
      />
      <PageHead
        eyebrow={`Project record · ${p.category.toUpperCase()} · ${p.division_name || p.division}`}
        title={`${p.zldp || 'No ZLDP number'} · ${p.developer}`}
        lead={p.site || undefined}
        chips={[`folder ${p.folder}`, `${p.drawings} files`, p.zldp ? 'ZLDP allocated' : 'no number allocated', `last activity ${when(p.last_activity)}`]}
        actions={
          prm && (
            <button className="btn primary" onClick={() => navigate('/new', { state: { inputs: inputsFromParams(prm, p.category), from: projectName(p) } })}>
              Use as design inputs
            </button>
          )
        }
      />

      <div className="tabs">
        {data.stages.map((s) => (
          <button key={s.key} className={`tab${s.key === stage ? ' on' : ''}`} onClick={() => setStage(s.key)}>
            {s.label} <span className="tabn">{s.revisions.length}</span>
          </button>
        ))}
      </div>

      <div className="split-r">
        <Card title={st ? `${st.label} folder` : ''} caption={st?.about}>
          {!st || st.revisions.length === 0 ? (
            <Empty title="This folder is empty">Nothing has been filed at this stage yet.</Empty>
          ) : (
            <div className="revs">
              {st.revisions.map((d) => (
                <div key={d.id} className={`rev${d.current ? ' cur' : ''}`}>
                  <span className="vbadge">v{String(d.version).padStart(2, '0')}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="name truncate" title={d.filename}>
                      {d.filename} {d.current && <Badge tone="blue">current</Badge>}
                    </div>
                    <div className="small muted truncate">
                      {d.original_name} · {d.note || d.kind} · {when(d.uploaded_at)} · {d.uploaded_by} {d.size ? `· ${kb(d.size)}` : ''}
                    </div>
                  </div>
                  <FileActions d={d} />
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="Parameters on file" caption={prm ? `${prm.stage} · ${prm.engine === 'mock' ? 'synthesised' : 'from the drawing'}` : ''}>
          {!prm ? (
            <p className="small muted">No parsed tentative or detailed drawing in this project yet.</p>
          ) : (
            <>
              <div className="kv"><span>Construction</span><b>{prm.construction || '–'}</b></div>
              <div className="kv"><span>Placement</span><b>{[prm.placement, prm.placement_sub].filter(Boolean).join(' · ') || '–'}</b></div>
              <div className="kv"><span>Floor</span><b>{prm.floor || '–'}</b></div>
              <div className="kv"><span>Room</span><b>{prm.length_m && prm.width_m ? `${prm.length_m} × ${prm.width_m} m · ${prm.area_sqm} m²` : prm.area_sqm ? `${prm.area_sqm} m²` : '–'}</b></div>
              <div className="kv"><span>Clear height</span><b>{prm.clear_height_mm ? `${prm.clear_height_mm} mm` : 'not present'}</b></div>
              <div className="kv"><span>Drawing no.</span><b className="mono small">{prm.drawing_no || '–'}</b></div>
              <div className="lbl" style={{ marginTop: 12 }}>Equipment</div>
              <ul className="eqlist">
                {prm.equipment.map((e, i) => (
                  <li key={i}>
                    <span className="mono">{e.quantity ?? 1} ×</span> {e.label} {e.provisional && <Badge tone="amber">provisional</Badge>}
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>
      </div>
    </>
  );
}
