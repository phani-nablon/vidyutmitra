import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { api, RULES_EXPORT_URL } from '../lib/api';
import { useAuth } from '../lib/auth';
import { Alert, Badge, Card, Empty, Field, PageHead, Spinner } from '../components/ui';
import type { Rule, RuleBook } from '../lib/types';

/*
 * Design rules (R5): view, add, edit, duplicate, delete, reset; export / import JSON; load from the
 * AEML workbook (Equip_DimensionRules.xlsx). Edits are kept in var/rules.json, the extracted seed is
 * never overwritten, and a rule that drives a live check changes that check as soon as it is saved.
 */

const fmt = (v: unknown) => (v == null || v === '' ? '' : typeof v === 'number' ? v.toLocaleString('en-IN') : String(v));
const isNum = (v: unknown) => typeof v === 'number' || (typeof v === 'string' && /^-?\d+(\.\d+)?$/.test(v));
const toConfirm = (r: Rule) => /confirm/i.test(r.ref_status || '');
const parseVal = (t: string) => {
  const s = t.trim();
  return s === '' ? null : /^-?\d+(\.\d+)?$/.test(s) ? Number(s) : s;
};

type Filter = '' | 'todo' | 'edited' | 'enforce';

function RuleForm({ book, rule, onCancel, onSave }: { book: RuleBook; rule: Partial<Rule>; onCancel: () => void; onSave: (r: Partial<Rule>) => Promise<void> }) {
  const [r, setR] = useState<Partial<Rule>>(rule);
  const [values, setValues] = useState(Object.entries(rule.values || {}).map(([k, v]) => `${k}: ${v}`).join('\n'));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof Rule, v: unknown) => setR((x) => ({ ...x, [k]: v }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    const vals: Record<string, string | number> = {};
    values.split('\n').forEach((ln) => {
      const m = ln.match(/^\s*([^:]+):\s*(.+)$/);
      if (m) vals[m[1].trim()] = parseVal(m[2]) as string | number;
    });
    setBusy(true);
    setErr(null);
    try {
      await onSave({ ...r, value: typeof r.value === 'string' ? parseVal(r.value) : r.value, values: Object.keys(vals).length ? vals : null });
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Could not save');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-back" role="dialog" aria-modal="true" aria-label={rule.id ? 'Edit rule' : 'Add rule'}>
      <form className="card glass modal" onSubmit={submit}>
        <div className="chead">
          <h3>{rule.id ? 'Edit rule' : 'Add rule'}</h3>
        </div>
        <div className="cbody stack">
          {err && <Alert tone="error">{err}</Alert>}
          <Field label="Section">
            <select className="select" value={r.section} onChange={(e) => set('section', e.target.value)}>
              {book.sections.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Rule">
            <input className="input" required value={r.title || ''} onChange={(e) => set('title', e.target.value)} />
          </Field>
          <Field label="Detail">
            <textarea className="textarea" rows={3} value={r.text || ''} onChange={(e) => set('text', e.target.value)} />
          </Field>
          <div className="grid-3">
            <Field label="Value">
              <input className="input" value={fmt(r.value)} onChange={(e) => set('value', e.target.value)} placeholder="number or text" />
            </Field>
            <Field label="Unit">
              <input className="input" value={r.unit || ''} onChange={(e) => set('unit', e.target.value || null)} placeholder="mm, m, %" />
            </Field>
            <Field label="Applies to">
              <select className="select" value={r.applies_to || 'CSS+DSS'} onChange={(e) => set('applies_to', e.target.value)}>
                <option>CSS+DSS</option>
                <option>CSS</option>
                <option>DSS</option>
              </select>
            </Field>
          </div>
          <Field label="Values by name" hint="One per line, e.g. front: 1000. Used for per-side clearances and dimensions.">
            <textarea className="textarea mono" rows={3} value={values} onChange={(e) => setValues(e.target.value)} />
          </Field>
          <div className="grid-3">
            <Field label="Group">
              <input className="input" value={r.group || ''} onChange={(e) => set('group', e.target.value || null)} />
            </Field>
            <Field label="Rule no.">
              <input className="input" value={r.rule_no || ''} onChange={(e) => set('rule_no', e.target.value || null)} />
            </Field>
            <Field label="Reference status">
              <select className="select" value={r.ref_status || ''} onChange={(e) => set('ref_status', e.target.value)}>
                <option value="">–</option>
                <option value="confirmed">Confirmed</option>
                <option value="to confirm">To confirm (runs as a warning)</option>
              </select>
            </Field>
          </div>
          <Field label="Reference (regulation / clause)">
            <input className="input" value={r.reference || ''} onChange={(e) => set('reference', e.target.value)} />
          </Field>
          <Field label="Source">
            <input className="input" value={r.source || ''} onChange={(e) => set('source', e.target.value)} />
          </Field>
          {r.enforce && <Alert tone="info">This value drives the live check <span className="mono">{r.enforce}</span>; saving changes it straight away.</Alert>}
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={onCancel}>
              Cancel
            </button>
            <button className="btn primary" disabled={busy}>
              {busy ? 'Saving…' : 'Save rule'}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

export function Rules() {
  const { can } = useAuth();
  const admin = can('admin');
  const [book, setBook] = useState<RuleBook | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [sec, setSec] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [who, setWho] = useState('');
  const [flt, setFlt] = useState<Filter>('');
  const [editing, setEditing] = useState<Partial<Rule> | null>(null);
  const [keepUser, setKeepUser] = useState(true);
  const jsonIn = useRef<HTMLInputElement>(null);
  const xlsIn = useRef<HTMLInputElement>(null);

  const load = () =>
    api
      .rules()
      .then((b) => {
        setBook(b);
        setSec((s) => (s && b.sections.some((x) => x.id === s) ? s : b.sections[0]?.id ?? null));
      })
      .catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  const hits = useMemo(() => {
    if (!book) return [];
    const n = q.trim().toLowerCase();
    return book.rules.filter((r) => {
      if (who && /^(CSS|DSS)$/.test(r.applies_to || '') && r.applies_to !== who) return false;
      if (flt === 'todo' && !toConfirm(r)) return false;
      if (flt === 'edited' && !(r.origin === 'user' || r.origin === 'edited')) return false;
      if (flt === 'enforce' && !r.enforce) return false;
      return !n || [r.title, r.text, fmt(r.value), r.reference, r.source, r.group, r.rule_no, JSON.stringify(r.values || '')].join(' ').toLowerCase().includes(n);
    });
  }, [book, q, who, flt]);
  const bySec = useMemo(() => {
    const m = new Map<string, Rule[]>();
    hits.forEach((r) => m.set(r.section, [...(m.get(r.section) || []), r]));
    return m;
  }, [hits]);

  // while searching, follow the hits to the first section that has any
  useEffect(() => {
    if (book && (q || flt || who) && sec && !(bySec.get(sec) || []).length) {
      const f = book.sections.find((s) => (bySec.get(s.id) || []).length);
      if (f) setSec(f.id);
    }
  }, [bySec]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error && !book) return <Alert tone="error">{error}</Alert>;
  if (!book) return <Spinner label="Loading the rules" />;

  const section = book.sections.find((s) => s.id === sec);
  const rows = bySec.get(sec || '') || [];
  const groups = new Map<string, Rule[]>();
  rows.forEach((r) => groups.set(r.group || '', [...(groups.get(r.group || '') || []), r]));
  const rule = book.rules.find((r) => r.id === sel);
  const todo = book.rules.filter(toConfirm).length;
  const edited = book.rules.filter((r) => r.origin === 'user' || r.origin === 'edited').length;

  async function act(fn: () => Promise<unknown>, msg: string) {
    setError(null);
    setNotice(null);
    try {
      await fn();
      if (msg) setNotice(msg);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed');
    }
  }

  async function upload(file: File | undefined, workbook: boolean) {
    if (!file) return;
    await act(async () => {
      const r = await api.importRules(file, keepUser, workbook);
      setNotice(`Loaded ${r.rules} rules in ${r.sections} sections from ${file.name}.`);
    }, '');
  }

  return (
    <>
      <PageHead
        eyebrow="Design rules · R5"
        title="The rule library the checks run on."
        lead="Seeded from the AEML equipment workbook, DCPR 2034 and NBC. Edits are kept separately, so reloading the workbook never loses them; rules marked to confirm run as warnings."
        chips={[`${book.rules.length} rules`, `${book.sections.length} sections`, `${todo} to confirm`, `${edited} edited or added`, book.edited ? 'edited copy in use' : 'as extracted']}
        actions={
          admin && (
            <>
              <button className="btn" onClick={() => xlsIn.current?.click()} title="Rebuild the rules from Equip_DimensionRules.xlsx">
                Load workbook
              </button>
              <button className="btn" onClick={() => jsonIn.current?.click()}>
                Import JSON
              </button>
              <a className="btn" href={RULES_EXPORT_URL}>
                Export JSON
              </a>
              <button className="btn primary" onClick={() => setEditing({ section: sec || book.sections[0].id, applies_to: 'CSS+DSS' })}>
                Add rule
              </button>
            </>
          )
        }
      />
      <input ref={xlsIn} type="file" accept=".xlsx" hidden onChange={(e) => { upload(e.target.files?.[0], true); e.target.value = ''; }} />
      <input ref={jsonIn} type="file" accept=".json" hidden onChange={(e) => { upload(e.target.files?.[0], false); e.target.value = ''; }} />

      {!admin && <Alert tone="info">You can read the rules. Adding, editing and loading them needs the admin role.</Alert>}
      {error && <Alert tone="error">{error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}

      <div className="rules-bar">
        <input className="input" style={{ maxWidth: 280 }} placeholder="Search rules" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="select" style={{ width: 150 }} value={who} onChange={(e) => setWho(e.target.value)} aria-label="Applies to">
          <option value="">CSS + DSS</option>
          <option value="CSS">CSS only</option>
          <option value="DSS">DSS only</option>
        </select>
        <div className="opts">
          {(
            [
              ['', 'All'],
              ['todo', 'To confirm'],
              ['edited', 'Edited / added'],
              ['enforce', 'Drives a check'],
            ] as Array<[Filter, string]>
          ).map(([k, l]) => (
            <button key={k} type="button" className={`opt${flt === k ? ' on' : ''}`} onClick={() => setFlt(k)}>
              {l}
            </button>
          ))}
        </div>
        <span className="grow" />
        {admin && (
          <>
            <label className="check-opt small" title="When loading a workbook or a JSON file, keep the rules people added by hand">
              <input type="checkbox" checked={keepUser} onChange={(e) => setKeepUser(e.target.checked)} />
              Keep hand-added rules on load
            </label>
            <button
              className="btn sm ghost"
              onClick={() => window.confirm('Discard every rule edit and go back to the extracted rules?') && act(() => api.resetRules(), 'Back to the extracted rules.')}
            >
              Reset to extracted
            </button>
          </>
        )}
      </div>

      <div className="rules-layout">
        <nav className="card glass rules-nav" aria-label="Rule sections">
          {book.sections.map((s) => {
            const n = (bySec.get(s.id) || []).length;
            return (
              <button key={s.id} className={`rsec${s.id === sec ? ' on' : ''}${n ? '' : ' dim'}`} onClick={() => setSec(s.id)}>
                <span>{s.title}</span>
                <span className="mono">{n}</span>
              </button>
            );
          })}
        </nav>

        <Card
          title={section?.title || ''}
          caption={`${rows.length} rule${rows.length === 1 ? '' : 's'}`}
          action={admin && <button className="btn sm" onClick={() => setEditing({ section: sec || undefined, applies_to: 'CSS+DSS' })}>+ Add here</button>}
          flush
        >
          {section?.about && <p className="small muted" style={{ padding: '10px 18px 0' }}>{section.about}</p>}
          {rows.length === 0 ? (
            <Empty title="No rules match here" />
          ) : (
            [...groups.entries()].map(([g, items]) => {
              const names = [...new Set(items.flatMap((r) => Object.keys(r.values || {})))].slice(0, 5);
              const anyNum = items.some((r) => isNum(r.value));
              return (
                <div key={g} className="table-scroll">
                  {g && <div className="rgroup">{g}</div>}
                  <table className="tbl rtbl">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>Rule</th>
                        {names.map((n) => (
                          <th key={n} className="num">{n}</th>
                        ))}
                        {anyNum && <th className="num">Value</th>}
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((r) => (
                        <tr key={r.id} className={`clickable${r.id === sel ? ' sel' : ''}`} onClick={() => setSel(r.id)}>
                          <td className="mono small">{r.rule_no || ''}</td>
                          <td>
                            <div className="name">{r.title}</div>
                            {r.value != null && r.value !== '' && !isNum(r.value) && <div className="small muted">{fmt(r.value)}</div>}
                          </td>
                          {names.map((n) => (
                            <td key={n} className="num mono">{fmt(r.values?.[n])}</td>
                          ))}
                          {anyNum && (
                            <td className="num">
                              {isNum(r.value) && (
                                <>
                                  <b className="mono">{fmt(r.value)}</b> <span className="small muted">{r.unit}</span>
                                </>
                              )}
                            </td>
                          )}
                          <td style={{ whiteSpace: 'nowrap' }}>
                            {toConfirm(r) && <Badge tone="amber">confirm</Badge>} {r.origin === 'user' && <Badge tone="violet">added</Badge>}
                            {r.origin === 'edited' && <Badge tone="violet">edited</Badge>} {r.enforce && <Badge tone="blue">check</Badge>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              );
            })
          )}
        </Card>

        <Card title="Rule" className="rules-detail">
          {!rule ? (
            <p className="small muted">Pick a rule to see its full text, reference and source.</p>
          ) : (
            <div className="stack">
              <span className="eyebrow">
                {book.sections.find((s) => s.id === rule.section)?.title}
                {rule.group ? ` › ${rule.group}` : ''}
              </span>
              <h3 style={{ fontSize: 16 }}>{rule.title}</h3>
              {isNum(rule.value) ? (
                <div className="bigval">
                  {fmt(rule.value)} <small>{rule.unit}</small>
                </div>
              ) : (
                rule.value != null && rule.value !== '' && rule.value !== rule.text && <p className="small strong">{fmt(rule.value)}</p>
              )}
              {rule.values && (
                <div>
                  {Object.entries(rule.values).map(([k, v]) => (
                    <div key={k} className="kv">
                      <span>{k}</span>
                      <b className="mono">
                        {fmt(v)} {rule.unit}
                      </b>
                    </div>
                  ))}
                </div>
              )}
              {rule.text && <p className="small">{rule.text}</p>}
              {rule.reference && (
                <div className="insight">
                  <b>Reference</b> {rule.reference} {rule.ref_status && <Badge tone={toConfirm(rule) ? 'amber' : 'blue'}>{toConfirm(rule) ? 'to confirm' : rule.ref_status}</Badge>}
                </div>
              )}
              <div className="small muted">
                Applies to {rule.applies_to || '–'} · {rule.source}
                {rule.enforce && (
                  <>
                    {' '}· drives <span className="mono">{rule.enforce}</span>
                  </>
                )}
              </div>
              {admin && (
                <div className="row">
                  <button className="btn primary sm" onClick={() => setEditing(rule)}>
                    Edit
                  </button>
                  <button className="btn sm" onClick={() => setEditing({ ...rule, id: undefined, title: `${rule.title} (copy)`, enforce: null })}>
                    Duplicate
                  </button>
                  <span className="grow" />
                  <button
                    className="btn sm ghost danger"
                    onClick={() =>
                      window.confirm(`Delete this rule?\n\n${rule.title}`) &&
                      act(async () => {
                        await api.deleteRule(rule.id);
                        setSel(null);
                      }, 'Rule deleted.')
                    }
                  >
                    Delete
                  </button>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>

      {editing && (
        <RuleForm
          book={book}
          rule={editing}
          onCancel={() => setEditing(null)}
          onSave={async (r) => {
            const { id, origin: _o, ...body } = r as Rule;
            void _o;
            const saved = id ? await api.updateRule(id, body) : await api.addRule(body);
            setEditing(null);
            setSel(saved.id);
            setSec(saved.section);
            setNotice(id ? 'Rule saved.' : 'Rule added.');
            await load();
          }}
        />
      )}
    </>
  );
}
