import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { api } from '../lib/api';
import { DesignInputs, EMPTY_INPUTS, inputsToQuery, openItems, type Inputs } from '../components/DesignInputs';
import { Alert, Badge, Card, Dropzone, Field, PageHead } from '../components/ui';
import { CheckList, ResultCard } from '../components/ResultCard';
import type { EquipmentRow, SearchResponse, SitePlan, StructuredQuery, Understood } from '../lib/types';

type Mode = 'inputs' | 'builder' | 'prompt';

const EXAMPLES = [
  'Indoor CSS on the ground floor of a residential building, 2 x 1500 kVA dry DT, 1 RMU, 4 LTP, room 11 x 8 m, clear height 4.5 m, rolling shutter, trench to DP road',
  'Open to sky substation, 1 x 990 kVA oil transformer, RMU, LTP, nalla 4 m away',
  'Metering CSS with RMU and CTPT on the stilt floor, 2 doors',
];

const EQUIPMENT_TYPES: Array<{ key: string; label: string; kva?: boolean; ways?: boolean }> = [
  { key: 'transformer', label: 'Transformer (DT)', kva: true },
  { key: 'rmu', label: 'Ring main unit (RMU)', ways: true },
  { key: 'ltp', label: 'LT panel / feeder pillar', ways: true },
  { key: 'metering', label: 'Metering (CT/PT)' },
  { key: 'packaged', label: 'Packaged substation', kva: true },
  { key: 'ht_switchgear', label: 'HT switchgear' },
  { key: 'apfc', label: 'Capacitor bank (APFC)' },
  { key: 'aux', label: 'Auxiliary (ACDB, battery, RTU)' },
];
const TYPE_LABEL = Object.fromEntries(EQUIPMENT_TYPES.map((t) => [t.key, t.label]));

const INSTALLATION = [
  ['indoor', 'Inside a building'],
  ['open_to_sky', 'Open to sky'],
  ['semi_open', 'Semi-open shed'],
];
const CONTEXT = [
  ['in_building', 'Part of a larger building'],
  ['standalone_building', 'Its own building'],
  ['outdoor_yard', 'Outdoor yard'],
  ['temporary', 'Temporary'],
];
const FLOORS = [
  ['basement', 'Basement'],
  ['stilt', 'Stilt'],
  ['podium', 'Podium'],
  ['ground', 'Ground'],
  ['first', 'First'],
  ['second', 'Second'],
  ['terrace', 'Terrace'],
];
const FEATURES = [
  ['rolling_shutter', 'Rolling shutter'],
  ['fire_exit', 'Fire exit door'],
  ['ventilation', 'Louvres or ventilation'],
  ['trench', 'Cable trench'],
  ['cutout', 'Slab cutout'],
  ['beam', 'Beams in the room'],
  ['staircase', 'Staircase'],
];
const DISTANCES = [
  ['nalla', 'Nalla or drain'],
  ['dp_road', 'DP road'],
  ['tree', 'Trees'],
  ['compound_wall', 'Compound wall'],
  ['structure', 'Neighbouring building'],
];

interface Builder {
  installation: string;
  context: string;
  floor: string;
  voltage: string;
  equipment: EquipmentRow[];
  length: string;
  width: string;
  height: string;
  shape: string;
  doors: string;
  platform: '' | 'yes' | 'no';
  features: Record<string, boolean>;
  distances: Record<string, string>;
  notes: string;
}

const EMPTY: Builder = {
  installation: '',
  context: '',
  floor: '',
  voltage: '',
  equipment: [
    { type: 'transformer', count: 1, kva: null },
    { type: 'rmu', count: 1 },
    { type: 'ltp', count: 1 },
  ],
  length: '',
  width: '',
  height: '',
  shape: '',
  doors: '',
  platform: '',
  features: {},
  distances: {},
  notes: '',
};

const num = (s: string) => (s.trim() === '' || Number.isNaN(Number(s)) ? undefined : Number(s));

/** Builder state -> the structured query the backend scores. Metres in the form, millimetres on the wire. */
function toStructured(b: Builder): StructuredQuery {
  const q: StructuredQuery = {};
  if (b.installation) q.installation = b.installation;
  if (b.context) q.context = b.context;
  if (b.floor) q.floor = b.floor;
  if (b.voltage) q.voltage_kv = Number(b.voltage);
  const items = b.equipment
    .filter((e) => e.count > 0)
    .map((e) => ({ type: e.type, count: e.count, kva: e.kva || null, dt_kind: e.dt_kind || null, ways: e.ways || null }));
  if (items.length) q.equipment_items = items;
  const L = num(b.length);
  const W = num(b.width);
  if (L && W) {
    q.length_mm = Math.max(L, W) * 1000;
    q.width_mm = Math.min(L, W) * 1000;
    q.area_m2 = Math.round(L * W * 10) / 10;
  }
  const H = num(b.height);
  if (H) q.clear_height_mm = H * 1000;
  if (b.shape) q.shape = b.shape;
  if (b.platform) q.platform = b.platform === 'yes';
  const civil: Record<string, boolean | number> = {};
  Object.entries(b.features).forEach(([k, v]) => v && (civil[k] = true));
  const doors = num(b.doors);
  if (doors) civil.door_count = doors;
  if (Object.keys(civil).length) q.civil = civil;
  const dist: Record<string, number> = {};
  Object.entries(b.distances).forEach(([k, v]) => {
    const m = num(v);
    if (m !== undefined) dist[k] = m * 1000;
  });
  if (Object.keys(dist).length) q.site_distances = dist;
  return q;
}

/** The same request as one plain sentence, so the person filling the form can see what will be searched. */
function sentence(b: Builder): string {
  const bits: string[] = [];
  const inst = INSTALLATION.find(([k]) => k === b.installation)?.[1];
  const floor = FLOORS.find(([k]) => k === b.floor)?.[1];
  const ctx = CONTEXT.find(([k]) => k === b.context)?.[1];
  if (inst || floor || ctx)
    bits.push([inst, floor && `on the ${floor.toLowerCase()} floor`, ctx && `(${ctx.toLowerCase()})`].filter(Boolean).join(' '));
  const eq = b.equipment
    .filter((e) => e.count > 0)
    .map((e) => `${e.count} × ${e.kva ? `${e.kva} kVA ` : ''}${e.dt_kind ? `${e.dt_kind} ` : ''}${TYPE_LABEL[e.type] || e.type}`);
  if (eq.length) bits.push(eq.join(', '));
  if (num(b.length) && num(b.width)) bits.push(`room ${b.length} × ${b.width} m`);
  if (num(b.height)) bits.push(`clear height ${b.height} m`);
  const f = FEATURES.filter(([k]) => b.features[k]).map(([, l]) => l.toLowerCase());
  if (f.length) bits.push(f.join(', '));
  DISTANCES.forEach(([k, l]) => num(b.distances[k] || '') !== undefined && bits.push(`${l.toLowerCase()} ${b.distances[k]} m away`));
  return bits.join('; ');
}

function Chips({ options, value, onChange }: { options: string[][]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="opts">
      {options.map(([k, label]) => (
        <button key={k} type="button" className={`opt${value === k ? ' on' : ''}`} onClick={() => onChange(value === k ? '' : k)}>
          {label}
        </button>
      ))}
    </div>
  );
}

function Section({ n, title, hint, children }: { n: number; title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="qb-section">
      <h3>
        <span className="qb-n">{n}</span>
        {title}
      </h3>
      {hint && <p className="small muted">{hint}</p>}
      {children}
    </section>
  );
}

interface Turn {
  role: 'user' | 'bot';
  text: string;
}

/** Page 1: describe a new site (prompt or builder) and get the closest historic designs. */
export function NewDesign() {
  const location = useLocation();
  const pre = (location.state as { inputs?: Partial<Inputs>; from?: string } | null) || null;
  const [mode, setMode] = useState<Mode>('inputs');
  const [inp, setInp] = useState<Inputs>(() => ({ ...EMPTY_INPUTS, ...(pre?.inputs || {}) }));
  const [understood, setUnderstood] = useState<Understood | null>(null);
  const [carried, setCarried] = useState<string[]>([]);
  const [b, setB] = useState<Builder>(EMPTY);
  const [prompt, setPrompt] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [session, setSession] = useState<string | null>(null);
  const [res, setRes] = useState<SearchResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [planFile, setPlanFile] = useState<File | null>(null);
  const [planColour, setPlanColour] = useState('red');
  const [planLength, setPlanLength] = useState('');
  const [plan, setPlan] = useState<SitePlan | null>(null);

  const summary = useMemo(() => sentence(b), [b]);
  const resultsTop = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (res && resultsTop.current && resultsTop.current.getBoundingClientRect().top < 0)
      resultsTop.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [res]);
  const set = <K extends keyof Builder>(k: K, v: Builder[K]) => setB((x) => ({ ...x, [k]: v }));
  const setRow = (i: number, patch: Partial<EquipmentRow>) =>
    set('equipment', b.equipment.map((e, j) => (j === i ? { ...e, ...patch } : e)));

  async function uploadPlan(file: File | null) {
    setPlanFile(file);
    setPlan(null);
    if (!file) return;
    try {
      setPlan(await api.siteplan(file, planColour, num(planLength) ? Number(planLength) : null));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Site plan could not be read');
    }
  }

  // the design request: read it back first (C8), then match, carrying the open input items onto the results
  async function readInputs() {
    const { text, structured } = inputsToQuery(inp);
    const u = await api.understand(text, structured);
    setUnderstood(u);
    return { text, structured };
  }

  async function runInputs(search: boolean) {
    setBusy(true);
    setError(null);
    try {
      const { text, structured } = await readInputs();
      if (search) {
        setCarried(openItems(inp));
        setRes(await api.search(text, structured, plan?.id));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not read the request');
    } finally {
      setBusy(false);
    }
  }

  async function runBuilder() {
    setCarried([]);
    setBusy(true);
    setError(null);
    try {
      setRes(await api.search(b.notes, toStructured(b), plan?.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed');
    } finally {
      setBusy(false);
    }
  }

  async function runPrompt(text = prompt) {
    if (!text.trim()) return;
    setCarried([]);
    setBusy(true);
    setError(null);
    setTurns((t) => [...t, { role: 'user', text }]);
    setPrompt('');
    try {
      const out = await api.chat(text, session, plan?.id);
      setSession(out.session_id);
      setTurns((t) => [...t, { role: 'bot', text: out.answer }]);
      if (out.search) setRes(out.search);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed');
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    setB(EMPTY);
    setInp(EMPTY_INPUTS);
    setUnderstood(null);
    setCarried([]);
    setTurns([]);
    setSession(null);
    setRes(null);
    setPlan(null);
    setPlanFile(null);
  }

  const sitePlanCard = (
    <Card title="Builder's site plan (optional)">
      <p className="small muted" style={{ marginTop: 0 }}>
        Mark the proposed substation area in one colour on the builder's plan. The outline becomes the room
        shape and the historic designs are laid over it.
      </p>
      <div className="row" style={{ gap: 12, marginBottom: 12 }}>
        <Field label="Marked in">
          <select className="select" value={planColour} onChange={(e) => setPlanColour(e.target.value)}>
            {['red', 'green', 'blue', 'magenta', 'cyan', 'yellow'].map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </Field>
        <Field label="Long side of the area (mm), for scale">
          <input className="input" type="number" value={planLength} onChange={(e) => setPlanLength(e.target.value)} placeholder="optional" />
        </Field>
      </div>
      <Dropzone onFile={uploadPlan} file={planFile} accept=".png,.jpg,.jpeg,.pdf" hint="PNG, JPG or PDF" label="Drop the site plan here" />
      {plan?.preview_url && <img className="plan-preview" src={plan.preview_url} alt="Detected site outline" />}
      {plan?.error && <Alert tone="warn">{String(plan.error)}</Alert>}
    </Card>
  );

  return (
    <>
      <PageHead
        eyebrow="Create new · page 1"
        title="Describe the new site."
        lead="Only what you fill in is compared, so leave anything you don't know. The closest historic designs come back with a verdict and the checks behind it."
        chips={['KG match, RAG fallback', 'partial queries are fine', 'site plan optional']}
      />
      <div className="tabs">
        <button className={`tab${mode === 'inputs' ? ' on' : ''}`} onClick={() => setMode('inputs')}>
          Design inputs
        </button>
        <button className={`tab${mode === 'builder' ? ' on' : ''}`} onClick={() => setMode('builder')}>
          Design rules
        </button>
        <button className={`tab${mode === 'prompt' ? ' on' : ''}`} onClick={() => setMode('prompt')}>
          Describe in words
        </button>
      </div>

      {error && <Alert tone="error">{error}</Alert>}
      {pre?.from && mode === 'inputs' && (
        <Alert tone="info">Design inputs taken from the repository project {pre.from}. Check them, then find matching designs.</Alert>
      )}

      <div className="split">
        <div className="stack">
          {mode === 'inputs' ? (
            <Card title="Design inputs" caption="C7 · input package, site type, text request" action={<button className="btn ghost sm" onClick={reset}>Start over</button>}>
              <DesignInputs value={inp} onChange={(v) => { setInp(v); setUnderstood(null); }} understood={understood} />
              <div className="row" style={{ marginTop: 14 }}>
                <button className="btn" disabled={busy || !inp.request.trim()} onClick={() => runInputs(false)}>
                  Read the request
                </button>
                <button className="btn primary grow" disabled={busy || (!inp.request.trim() && !inp.siteType && !plan)} onClick={() => runInputs(true)}>
                  {busy ? 'Working…' : 'Find matching designs'}
                </button>
              </div>
            </Card>
          ) : mode === 'builder' ? (
            <Card title="Query builder" action={<button className="btn ghost sm" onClick={reset}>Start over</button>}>
              <Section n={1} title="Where will it sit?">
                <Field label="Installation">
                  <Chips options={INSTALLATION} value={b.installation} onChange={(v) => set('installation', v)} />
                </Field>
                <Field label="Floor">
                  <Chips options={FLOORS} value={b.floor} onChange={(v) => set('floor', v)} />
                </Field>
                <Field label="Building">
                  <Chips options={CONTEXT} value={b.context} onChange={(v) => set('context', v)} />
                </Field>
              </Section>

              <Section n={2} title="What equipment does it need?" hint="As Network Planning sends it. kVA only matters for transformers.">
                <div className="eq-rows">
                  {b.equipment.map((e, i) => {
                    const t = EQUIPMENT_TYPES.find((x) => x.key === e.type);
                    return (
                      <div className="eq-row" key={i}>
                        <input
                          className="input eq-count"
                          type="number"
                          min={0}
                          value={e.count}
                          onChange={(ev) => setRow(i, { count: Math.max(0, Number(ev.target.value)) })}
                          aria-label="How many"
                        />
                        <span className="muted">×</span>
                        <select className="select" value={e.type} onChange={(ev) => setRow(i, { type: ev.target.value, kva: null, ways: null })}>
                          {EQUIPMENT_TYPES.map((x) => (
                            <option key={x.key} value={x.key}>
                              {x.label}
                            </option>
                          ))}
                        </select>
                        {t?.kva && (
                          <input
                            className="input eq-small"
                            type="number"
                            placeholder="kVA"
                            value={e.kva ?? ''}
                            onChange={(ev) => setRow(i, { kva: num(ev.target.value) ?? null })}
                          />
                        )}
                        {e.type === 'transformer' && (
                          <select className="select eq-small" value={e.dt_kind ?? ''} onChange={(ev) => setRow(i, { dt_kind: ev.target.value || null })}>
                            <option value="">type?</option>
                            <option value="dry">Dry</option>
                            <option value="oil">Oil</option>
                            <option value="ester">Ester</option>
                          </select>
                        )}
                        {t?.ways && (
                          <input
                            className="input eq-small"
                            type="number"
                            placeholder="ways"
                            value={e.ways ?? ''}
                            onChange={(ev) => setRow(i, { ways: num(ev.target.value) ?? null })}
                          />
                        )}
                        <button className="btn ghost sm" onClick={() => set('equipment', b.equipment.filter((_, j) => j !== i))} title="Remove">
                          ✕
                        </button>
                      </div>
                    );
                  })}
                </div>
                <button className="btn sm" onClick={() => set('equipment', [...b.equipment, { type: 'ltp', count: 1 }])}>
                  Add equipment
                </button>
              </Section>

              <Section n={3} title="How big is the room?" hint="In metres. Skip it if you are uploading a site plan.">
                <div className="grid-3">
                  <Field label="Length (m)">
                    <input className="input" type="number" value={b.length} onChange={(e) => set('length', e.target.value)} placeholder="11" />
                  </Field>
                  <Field label="Width (m)">
                    <input className="input" type="number" value={b.width} onChange={(e) => set('width', e.target.value)} placeholder="8" />
                  </Field>
                  <Field label="Clear height (m)">
                    <input className="input" type="number" value={b.height} onChange={(e) => set('height', e.target.value)} placeholder="4.5" />
                  </Field>
                </div>
                <Field label="Shape">
                  <Chips
                    options={[
                      ['rectangular', 'Rectangular'],
                      ['L_shape', 'L-shaped'],
                      ['chamfered', 'Angled / irregular'],
                    ]}
                    value={b.shape}
                    onChange={(v) => set('shape', v)}
                  />
                </Field>
              </Section>

              <Section n={4} title="Doors and civil features">
                <div className="checks-grid">
                  {FEATURES.map(([k, label]) => (
                    <label key={k} className="check-opt">
                      <input type="checkbox" checked={!!b.features[k]} onChange={(e) => set('features', { ...b.features, [k]: e.target.checked })} />
                      {label}
                    </label>
                  ))}
                </div>
                <div className="grid-3">
                  <Field label="Number of doors">
                    <input className="input" type="number" value={b.doors} onChange={(e) => set('doors', e.target.value)} />
                  </Field>
                  <Field label="Raised platform">
                    <select className="select" value={b.platform} onChange={(e) => set('platform', e.target.value as Builder['platform'])}>
                      <option value="">Don't know</option>
                      <option value="yes">Yes</option>
                      <option value="no">No</option>
                    </select>
                  </Field>
                  <Field label="Voltage">
                    <select className="select" value={b.voltage} onChange={(e) => set('voltage', e.target.value)}>
                      <option value="">Don't know</option>
                      <option value="11">11 kV</option>
                      <option value="33">33 kV</option>
                    </select>
                  </Field>
                </div>
              </Section>

              <Section n={5} title="What is nearby?" hint="Distance from the substation in metres. These are checked against the plot clearance norms.">
                <div className="grid-3">
                  {DISTANCES.map(([k, label]) => (
                    <Field key={k} label={label}>
                      <input
                        className="input"
                        type="number"
                        value={b.distances[k] ?? ''}
                        onChange={(e) => set('distances', { ...b.distances, [k]: e.target.value })}
                      />
                    </Field>
                  ))}
                </div>
              </Section>

              <Section n={6} title="Anything else?">
                <textarea
                  className="textarea"
                  rows={2}
                  value={b.notes}
                  onChange={(e) => set('notes', e.target.value)}
                  placeholder="Free text, e.g. 24x7 access from the DP road, transformer door 3 m wide"
                />
              </Section>

              <div className="qb-summary">
                <span className="small muted">You are asking for</span>
                <p>{summary || 'nothing yet. Fill in any of the sections above.'}</p>
              </div>
              <button className="btn primary block" disabled={busy || (!summary && !plan && !b.notes.trim())} onClick={runBuilder}>
                {busy ? 'Searching…' : 'Find matching designs'}
              </button>
            </Card>
          ) : (
            <Card title="Describe the site" action={<button className="btn ghost sm" onClick={reset}>New conversation</button>}>
              {turns.length === 0 && (
                <div className="stack">
                  <p className="small muted" style={{ margin: 0 }}>
                    Write it the way you would explain it to a colleague. Try one of these:
                  </p>
                  {EXAMPLES.map((ex) => (
                    <button key={ex} className="example" onClick={() => runPrompt(ex)} disabled={busy}>
                      {ex}
                    </button>
                  ))}
                </div>
              )}
              <div className="thread">
                {turns.map((t, i) => (
                  <div key={i} className={`bubble ${t.role}`}>
                    {t.text}
                  </div>
                ))}
                {busy && <div className="bubble bot muted">Searching the library…</div>}
              </div>
              <textarea
                className="textarea"
                rows={3}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    runPrompt();
                  }
                }}
                placeholder={turns.length ? 'Ask a follow-up, or change the requirement ("make it 2 transformers")' : 'Describe the new site…'}
              />
              <div className="row" style={{ justifyContent: 'flex-end', marginTop: 8 }}>
                <span className="small muted grow">Enter to send, Shift+Enter for a new line</span>
                <button className="btn primary" disabled={busy || !prompt.trim()} onClick={() => runPrompt()}>
                  Send
                </button>
              </div>
            </Card>
          )}
          {sitePlanCard}
        </div>

        <div className="stack" ref={resultsTop} style={{ scrollMarginTop: 80 }}>
          {!res && !busy && (
            <div className="card glass cbody results-empty">
              <h3>Matching designs appear here</h3>
              <p className="small muted">
                Each one comes with a verdict (reuse, adapt or design new), the checks that pass or fail, the
                original equipment layout and the historic room laid over your site.
              </p>
            </div>
          )}
          {res && (
            <>
              <div className="card glass cbody">
                <div className="row spread">
                  <h3>
                    {res.results.length} designs <span className="small muted">· {res.engine.toUpperCase()} · {res.elapsed_ms} ms</span>
                  </h3>
                </div>
                {res.asked.length > 0 && (
                  <div className="understood">
                    <span className="small muted">What was understood:</span>
                    {res.asked.map((a) => (
                      <Badge key={a}>{a}</Badge>
                    ))}
                  </div>
                )}
                <CheckList checks={res.feasibility} />
                {carried.length > 0 && (
                  <div className="carried">
                    <span className="lbl">Open items from the input package</span>
                    <ul>
                      {carried.map((c) => (
                        <li key={c}>{c}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
              {res.results.length === 0 && (
                <Alert tone="warn">No historic design matched. Add the installation, floor, equipment or room size.</Alert>
              )}
              {res.results.map((r, i) => (
                <ResultCard key={`${r.doc_id}-${r.substation_id}`} rank={i + 1} result={r} />
              ))}
            </>
          )}
        </div>
      </div>
    </>
  );
}
