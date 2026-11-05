import type { ReactNode } from 'react';
import { Alert, Badge, Field } from './ui';
import type { StructuredQuery, Understood } from '../lib/types';

/*
 * The design request as the detailed design defines it (screen C7): the AEML mandatory input package
 * marked Yes / No / NA, the site type, and the equipment typed the way Network Planning sends it. A
 * missing mandatory document does not block a tentative run; it is carried onto the results as an open
 * item. Fields the retrieval scores go into the structured query; the rest (door mode and positions,
 * stage, constraints) travel with the request and are shown back, not guessed into the match.
 */

export type Doc = 'yes' | 'no' | 'na' | '';
export interface Inputs {
  category: 'css' | 'dss';
  siteType: '' | 'open_to_sky' | 'in_building';
  openKind: 'rg' | 'open_plot';
  below: '' | 'basement' | 'earth';
  floor: '' | 'ground' | 'stilt' | 'stilt2' | 'stilt3' | 'stilt4' | 'basement';
  clearHeight: string;
  length: string;
  width: string;
  request: string;
  doorMode: '' | 'manual' | 'auto';
  mainDoor: string;
  emergencyDoor: string;
  stage: 'tentative' | 'detailed' | 'pwd';
  constraints: string;
  docs: Record<string, Doc>;
}

export const EMPTY_INPUTS: Inputs = {
  category: 'css',
  siteType: '',
  openKind: 'open_plot',
  below: '',
  floor: '',
  clearHeight: '',
  length: '',
  width: '',
  request: '',
  doorMode: '',
  mainDoor: '',
  emergencyDoor: '',
  stage: 'tentative',
  constraints: '',
  docs: {},
};

export const NP_EXAMPLE = 'PFA and place following equipment:\n1. 1.5 MVA DT – 3 nos\n2. 2+2 RMU – 1 nos\n3. 1+3 RMU – 1 nos';

interface DocDef {
  id: string;
  name: string;
  stage: 'tentative' | 'detailed';
  reads: string;
  applies: (i: Inputs) => boolean;
  optional?: boolean;
}

// Table 1 of the detailed design: AEML mandatory input package.
export const PACKAGE: DocDef[] = [
  { id: 'T1', name: 'Master layout of the plot', stage: 'tentative', reads: 'Plot boundary, DP road, internal roads, nalla, structures, trees', applies: () => true },
  { id: 'T2', name: 'Block plan', stage: 'tentative', reads: 'Building footprint and position of the substation space', applies: () => true },
  { id: 'T3', name: 'Ground floor plan with basement line', stage: 'tentative', reads: 'Room outline, columns, doors, basement extent', applies: () => true },
  { id: 'T4', name: 'X-X and Y-Y sections', stage: 'tentative', reads: 'Floor and slab levels, clear height, beam depths', applies: (i) => i.siteType === 'in_building' },
  { id: 'T5', name: 'Beam layout', stage: 'tentative', reads: 'Beam positions and depths; cut-out feasibility', applies: (i) => i.siteType === 'in_building' },
  { id: 'T6', name: 'Basement plan and sections', stage: 'tentative', reads: 'What lies below: basement or mother earth', applies: (i) => i.below === 'basement' || i.floor === 'basement' },
  { id: 'T7', name: 'Logistics route for major equipment (KAM verified)', stage: 'tentative', reads: 'Route and door width for transformer movement', applies: () => true },
  { id: 'T8', name: 'Cable trench / tray route, DP road to CSS', stage: 'tentative', reads: 'Cable entry point and route length', applies: () => true },
  { id: 'T9', name: 'Rising mains / load sheet', stage: 'tentative', reads: 'Load to confirm equipment rating', applies: () => true, optional: true },
  { id: 'D1', name: 'Service cable route, CSS to EFU / meter cabin', stage: 'detailed', reads: 'LT cable route and length for BOQ', applies: () => true },
  { id: 'D2', name: 'Proposed earthing pit locations', stage: 'detailed', reads: 'Earth pits and strip route', applies: () => true },
  { id: 'D3', name: 'Ventilation arrangement', stage: 'detailed', reads: 'Louvres, exhaust fans and their positions', applies: () => true },
];

export const docsFor = (i: Inputs) => PACKAGE.filter((d) => d.stage === 'tentative' || i.stage !== 'tentative');

/** Mandatory documents not marked Yes: carried onto the results as open items. */
export function openItems(i: Inputs): string[] {
  return docsFor(i)
    .filter((d) => d.applies(i) && !d.optional && i.docs[d.id] !== 'yes' && i.docs[d.id] !== 'na')
    .map((d) => `${d.id} ${d.name}: ${i.docs[d.id] === 'no' ? 'not provided' : 'not marked'}`);
}

const num = (s: string) => (s.trim() === '' || Number.isNaN(Number(s)) ? undefined : Number(s));

export function inputsToQuery(i: Inputs): { text: string; structured: StructuredQuery } {
  const q: StructuredQuery = {};
  if (i.siteType === 'open_to_sky') {
    q.installation = 'open_to_sky';
    q.context = 'outdoor_yard';
  } else if (i.siteType === 'in_building') {
    q.installation = 'indoor';
    q.context = 'in_building';
  }
  if (i.floor) q.floor = i.floor.startsWith('stilt') ? 'stilt' : i.floor;
  const h = num(i.clearHeight);
  if (h) q.clear_height_mm = h < 100 ? h * 1000 : h;
  const L = num(i.length);
  const W = num(i.width);
  if (L && W) {
    q.length_mm = Math.max(L, W) * 1000;
    q.width_mm = Math.min(L, W) * 1000;
    q.area_m2 = Math.round(L * W * 10) / 10;
  }
  const civil: Record<string, boolean | number> = {};
  if (i.emergencyDoor) civil.fire_exit = true;
  if (i.mainDoor || i.emergencyDoor) civil.door_count = (i.mainDoor ? 1 : 0) + (i.emergencyDoor ? 1 : 0);
  if (Object.keys(civil).length) q.civil = civil;
  return { text: i.request, structured: q };
}

function Opts<T extends string>({ value, options, onChange }: { value: T; options: Array<[T, string]>; onChange: (v: T) => void }) {
  return (
    <div className="opts">
      {options.map(([k, l]) => (
        <button key={k} type="button" className={`opt${value === k ? ' on' : ''}`} onClick={() => onChange(k)}>
          {l}
        </button>
      ))}
    </div>
  );
}

function Sec({ n, title, hint, children }: { n: number; title: string; hint?: string; children: ReactNode }) {
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

const WALLS: Array<[string, string]> = [
  ['', 'Not stated'],
  ['N', 'North'],
  ['E', 'East'],
  ['S', 'South'],
  ['W', 'West'],
];

export function DesignInputs({ value: i, onChange, understood }: { value: Inputs; onChange: (v: Inputs) => void; understood: Understood | null }) {
  const set = <K extends keyof Inputs>(k: K, v: Inputs[K]) => onChange({ ...i, [k]: v });
  const docs = docsFor(i);
  const open = openItems(i);

  return (
    <div>
      <Sec n={1} title="Substation and stage">
        <Field label="Category">
          <Opts value={i.category} options={[['css', 'CSS · consumer'], ['dss', 'DSS · distribution']]} onChange={(v) => set('category', v)} />
        </Field>
        <Field label="Target stage">
          <Opts value={i.stage} options={[['tentative', 'Tentative'], ['detailed', 'Detailed'], ['pwd', 'PWD submission']]} onChange={(v) => set('stage', v)} />
        </Field>
        {i.category === 'dss' && <Alert tone="info">The retrieval library is built from CSS drawings, so a DSS request is matched on equipment and site fields only.</Alert>}
        {i.stage === 'pwd' && (
          <Alert tone="warn">A PWD drawing is derived from an approved detailed layout and nothing else. Matching here finds the detailed layout to start from.</Alert>
        )}
      </Sec>

      <Sec n={2} title="Site type" hint="From the AEML rule sheet.">
        <Opts
          value={i.siteType}
          options={[['open_to_sky', 'Open to sky'], ['in_building', 'Part of building']]}
          onChange={(v) => onChange({ ...i, siteType: v, below: v === 'in_building' ? i.below : '' })}
        />
        {i.siteType === 'open_to_sky' && (
          <Field label="Open to sky on">
            <Opts value={i.openKind} options={[['open_plot', 'Open plot'], ['rg', 'RG (recreational ground)']]} onChange={(v) => set('openKind', v)} />
          </Field>
        )}
        {i.siteType === 'in_building' && (
          <Field label="What is below">
            <Opts value={i.below} options={[['basement', 'Basement below'], ['earth', 'Mother earth below']]} onChange={(v) => set('below', v)} />
          </Field>
        )}
        <Field label="Floor">
          <Opts
            value={i.floor}
            options={[['ground', 'Ground'], ['stilt2', 'Stilt +2'], ['stilt3', 'Stilt +3'], ['stilt4', 'Stilt +4'], ['basement', 'Basement']]}
            onChange={(v) => set('floor', v)}
          />
        </Field>
        <div className="grid-3">
          <Field label="Clear height (m)">
            <input className="input" type="number" value={i.clearHeight} onChange={(e) => set('clearHeight', e.target.value)} placeholder="4.2" />
          </Field>
          <Field label="Space length (m)">
            <input className="input" type="number" value={i.length} onChange={(e) => set('length', e.target.value)} placeholder="optional" />
          </Field>
          <Field label="Space width (m)">
            <input className="input" type="number" value={i.width} onChange={(e) => set('width', e.target.value)} placeholder="optional" />
          </Field>
        </div>
      </Sec>

      <Sec n={3} title="Text request" hint="Paste it the way Network Planning sends it. Each line is read into the equipment library and shown back for confirmation.">
        <textarea className="textarea mono" rows={5} value={i.request} onChange={(e) => set('request', e.target.value)} placeholder={NP_EXAMPLE} />
        {!i.request && (
          <button type="button" className="btn sm" style={{ alignSelf: 'flex-start' }} onClick={() => set('request', NP_EXAMPLE)}>
            Use the shared example
          </button>
        )}
        <div className="grid-3">
          <Field label="Door mode">
            <select className="select" value={i.doorMode} onChange={(e) => set('doorMode', e.target.value as Inputs['doorMode'])}>
              <option value="">Not stated</option>
              <option value="manual">Manual</option>
              <option value="auto">Auto</option>
            </select>
          </Field>
          <Field label="Main door">
            <select className="select" value={i.mainDoor} onChange={(e) => set('mainDoor', e.target.value)}>
              {WALLS.map(([k, l]) => (
                <option key={k} value={k}>{l}</option>
              ))}
            </select>
          </Field>
          <Field label="Emergency door">
            <select className="select" value={i.emergencyDoor} onChange={(e) => set('emergencyDoor', e.target.value)}>
              {WALLS.map(([k, l]) => (
                <option key={k} value={k}>{l}</option>
              ))}
            </select>
          </Field>
        </div>
        <Field label="Constraints">
          <input className="input" value={i.constraints} onChange={(e) => set('constraints', e.target.value)} placeholder="e.g. keep the east wall clear" />
        </Field>
      </Sec>

      <Sec n={4} title="Mandatory input package" hint="Mark each document. A missing one does not block a tentative run; it is carried onto the results as an open item.">
        <div className="pkg">
          {docs.map((d) => {
            const applies = d.applies(i);
            const v = i.docs[d.id] || (applies ? '' : 'na');
            return (
              <div key={d.id} className={`pkg-row${applies ? '' : ' na'}`}>
                <span className="mono pkg-id">{d.id}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="name">
                    {d.name} {d.optional && <span className="small muted">(where applicable)</span>}
                  </div>
                  <div className="small muted">{d.reads}</div>
                  {d.id === 'T1' && <div className="small" style={{ color: 'var(--brand-ink)' }}>Attach the plot with the CSS area marked in the site plan card to compare outlines.</div>}
                </div>
                <div className="yn" role="group" aria-label={`${d.id} provided`}>
                  {(['yes', 'no', 'na'] as const).map((k) => (
                    <button key={k} type="button" className={`yn-b ${k}${v === k ? ' on' : ''}`} onClick={() => set('docs', { ...i.docs, [d.id]: k })}>
                      {k === 'na' ? 'NA' : k === 'yes' ? 'Yes' : 'No'}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
        {open.length > 0 ? (
          <div className="openitems">
            <Badge tone="amber">{open.length} open item{open.length === 1 ? '' : 's'}</Badge> carried onto the results
          </div>
        ) : (
          <div className="openitems">
            <Badge tone="blue">package complete</Badge>
          </div>
        )}
      </Sec>

      {understood && (
        <div className="understood-box">
          <span className="lbl">Read from the request · confirm before matching</span>
          {understood.equipment.length > 0 ? (
            <table className="tbl" style={{ marginTop: 6 }}>
              <thead>
                <tr>
                  <th>Equipment</th>
                  <th className="num">Qty</th>
                  <th>Rating / config</th>
                </tr>
              </thead>
              <tbody>
                {understood.equipment.map((e, k) => (
                  <tr key={k}>
                    <td className="name">{e.type === 'ltp' ? 'LT panel' : e.type === 'rmu' ? 'RMU' : e.type === 'transformer' ? `${e.dt_kind ? `${e.dt_kind} ` : ''}DT` : e.type}</td>
                    <td className="num mono">{e.count}</td>
                    <td className="mono small">{[e.kva && `${e.kva} kVA`, e.subtype, e.ways && `${e.ways}-way`].filter(Boolean).join(' · ') || '–'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="small" style={{ color: 'var(--amber-ink)' }}>No equipment was recognised in the text request.</p>
          )}
          <div className="understood">
            {understood.asked.map((a) => (
              <span key={a} className="pill p-quar">{a}</span>
            ))}
            {!understood.fields.clear_height_mm && <span className="pill p-warn">clear height not present</span>}
          </div>
        </div>
      )}
    </div>
  );
}
