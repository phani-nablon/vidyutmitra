import { useRef, useState, type ReactNode } from 'react';

/* Thin React wrappers over the Nablon Meridian components (theme/meridian.css). */

/** Meridian card: white `.card.glass`, `.chead` title row, `.cbody` content. */
export function Card({
  title,
  action,
  caption,
  children,
  flush,
  className = '',
}: {
  title?: ReactNode;
  action?: ReactNode;
  caption?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  className?: string;
}) {
  return (
    <section className={`card glass ${className}`}>
      {title && (
        <header className="chead">
          <h3>{title}</h3>
          {caption && <span className="r">{caption}</span>}
          {action && <span className="chead-act">{action}</span>}
        </header>
      )}
      {flush ? children : <div className="cbody">{children}</div>}
    </section>
  );
}

type Tone = 'default' | 'blue' | 'violet' | 'green' | 'amber' | 'red';
// Meridian's reserved signals: blue = live / pass (there is no green), amber = needs you,
// violet = in progress, red = fail, slate = neutral.
const PILL: Record<Tone, string> = {
  default: 'p-quar',
  blue: 'p-live',
  green: 'p-live',
  violet: 'p-verify',
  amber: 'p-warn',
  red: 'p-fail',
};

/** State pill with its dot. */
export function Badge({ tone = 'default', children, dot = true }: { tone?: Tone; children: ReactNode; dot?: boolean }) {
  return (
    <span className={`pill ${PILL[tone]}`}>
      {dot && <span className="d" />}
      {children}
    </span>
  );
}

/** Page header: Meridian's flat `.hero` (orb + eyebrow, lead line, meta chips). */
export function PageHead({
  eyebrow,
  title,
  lead,
  chips,
  actions,
}: {
  eyebrow: string;
  title: ReactNode;
  lead?: ReactNode;
  chips?: ReactNode[];
  actions?: ReactNode;
}) {
  return (
    <section className="hero">
      <div className="top">
        <span className="orb sm" />
        <p className="eyebrow">{eyebrow}</p>
        {actions && <div className="hero-act">{actions}</div>}
      </div>
      <p className="hl">
        <b>{title}</b>
        {lead && <> {lead}</>}
      </p>
      {chips && chips.length > 0 && (
        <div className="metachips">
          {chips.map((c, i) => (
            <span key={i} className="metachip">
              {c}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

/** Section header with the gradient accent bar. */
export function Section({ title, sub, right }: { title: string; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="sech">
      <span className="abar" />
      <h2>{title}</h2>
      {sub && <span className="sub">{sub}</span>}
      {right && <span className="rgt">{right}</span>}
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action && <div style={{ marginTop: 16 }}>{action}</div>}
    </div>
  );
}

export function Alert({ tone = 'info', children }: { tone?: 'info' | 'error' | 'warn' | 'success'; children: ReactNode }) {
  return <div className={`alert ${tone}`}>{children}</div>;
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}

export function ScoreBar({ score }: { score: number; threshold?: number }) {
  return (
    <div className="scorebar">
      <div className="bar">
        <i style={{ width: `${Math.min(score, 100)}%`, background: 'var(--grad)' }} />
      </div>
      <span className="mono">{score}%</span>
    </div>
  );
}

export function Dropzone({
  onFile,
  accept = '.pdf,.png,.jpg,.jpeg',
  label = 'Drop a file here, or click to browse',
  hint,
  file,
}: {
  onFile: (file: File | null) => void;
  accept?: string;
  label?: string;
  hint?: string;
  file?: File | null;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      className={`dropzone${over ? ' over' : ''}`}
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const dropped = e.dataTransfer.files?.[0];
        if (dropped) onFile(dropped);
      }}
    >
      <input ref={inputRef} type="file" accept={accept} hidden onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
      <strong>{file ? file.name : label}</strong>
      <span className="small muted">{file ? 'Click to choose a different file' : hint}</span>
    </div>
  );
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="empty">
      <span className="orb xs spin" />
      <p>{label}…</p>
    </div>
  );
}

/** Nablon SLIDe brand mark (substation glyph in the Meridian tile). */
export function BrandMark() {
  return (
    <svg viewBox="0 0 40 40" fill="none" aria-hidden="true">
      <defs>
        <linearGradient id="mkg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#3B82F6" />
          <stop offset="1" stopColor="#1D4ED8" />
        </linearGradient>
      </defs>
      <rect x="3" y="3" width="34" height="34" rx="10" fill="url(#mkg)" />
      <rect x="3.7" y="3.7" width="32.6" height="32.6" rx="9.3" fill="none" stroke="#fff" strokeOpacity=".2" />
      <rect x="11" y="12" width="18" height="16" rx="2.6" fill="none" stroke="#fff" strokeWidth="1.9" />
      <rect x="14" y="15.4" width="5.4" height="5" rx="1.1" fill="#fff" />
      <circle cx="24.4" cy="17.9" r="2.1" fill="#fff" />
      <path d="M14 23.4h10.4" stroke="#fff" strokeWidth="1.9" strokeLinecap="round" />
    </svg>
  );
}
