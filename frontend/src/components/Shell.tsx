import { useEffect, useState, type ReactNode } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { ROLE_LABEL, useAuth } from '../lib/auth';
import { BrandMark } from './ui';
import type { Health } from '../lib/types';

const I = {
  home: (
    <svg viewBox="0 0 24 24">
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
    </svg>
  ),
  create: (
    <svg viewBox="0 0 24 24">
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5M11 8v6M8 11h6" />
    </svg>
  ),
  review: (
    <svg viewBox="0 0 24 24">
      <path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 8h10M7 12h6" />
    </svg>
  ),
  repo: (
    <svg viewBox="0 0 24 24">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  ),
  rules: (
    <svg viewBox="0 0 24 24">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  ),
  classic: (
    <svg viewBox="0 0 24 24">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
    </svg>
  ),
};

function crumb(path: string): string {
  if (path.startsWith('/repo/p/')) return 'SLIDe / Repository / Project record';
  if (path.startsWith('/repo/')) return `SLIDe / Repository / ${path.split('/').slice(2).map(decodeURIComponent).join(' / ')}`;
  if (path.startsWith('/repo')) return 'SLIDe / Repository';
  if (path.startsWith('/rules')) return 'SLIDe / Rules / Design rules';
  if (path.startsWith('/new')) return 'SLIDe / Design / Create new';
  if (path.startsWith('/review/')) return 'SLIDe / Library / Review drawings / Drawing';
  if (path.startsWith('/review')) return 'SLIDe / Library / Review drawings';
  return 'SLIDe / Home';
}

function Nl({ to, icon, children, end, mark }: { to: string; icon: ReactNode; children: ReactNode; end?: boolean; mark?: boolean }) {
  return (
    <NavLink to={to} end={end} className={({ isActive }) => `nlink${isActive ? ' on' : ''}`}>
      {icon}
      {children}
      {mark && <span className="dotmark" title="Drawings are waiting for review" />}
    </NavLink>
  );
}

/** Meridian shell: left sidebar, slim sticky topbar, content `.wrap`. */
export function Shell() {
  const { user, authOn, signOut } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [health, setHealth] = useState<Health | null>(null);
  const [pending, setPending] = useState(0);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    api
      .queue()
      .then((q) => setPending(q.documents.filter((d) => d.open_actions > 0).length))
      .catch(() => setPending(0));
  }, [pathname]);

  const graphUp = health?.engines?.kg?.ok !== false;

  return (
    <div className="app">
      <aside className="side">
        <div className="brand">
          <BrandMark />
          <div>
            <div className="bt">Nablon SLIDe</div>
            <div className="bs">Substation design retrieval</div>
          </div>
        </div>
        <nav className="nav">
          <Nl to="/" end icon={I.home}>
            Home
          </Nl>
          <div className="navhead">Repository</div>
          <Nl to="/repo" icon={I.repo}>
            Browse repository
          </Nl>
          <div className="navhead">Design</div>
          <Nl to="/new" icon={I.create}>
            Create new
          </Nl>
          <div className="navhead">Library</div>
          <Nl to="/review" icon={I.review} mark={pending > 0}>
            Review drawings
          </Nl>
          <div className="navhead">Rules</div>
          <Nl to="/rules" icon={I.rules}>
            Design rules
          </Nl>
          <div className="navhead">Tools</div>
          <a className="nlink" href="/classic">
            {I.classic}
            Classic tools
          </a>
        </nav>
        <div className="grow" />
        {user && (
          <div className="side-user">
            <span className="avatar">{user.name.slice(0, 1).toUpperCase()}</span>
            <div style={{ minWidth: 0 }}>
              <div className="su-name truncate">{user.name}</div>
              <div className="su-role">{ROLE_LABEL[user.role]}</div>
            </div>
            {authOn && (
              <button
                className="btn ghost sm"
                title="Sign out"
                onClick={async () => {
                  await signOut();
                  navigate('/login', { replace: true });
                }}
              >
                Sign out
              </button>
            )}
          </div>
        )}
      </aside>

      <main className="main">
        <header className="topbar">
          <span className="tb-crumb">{crumb(pathname)}</span>
          <div className="tb-right">
            {health && (
              <>
                <span className={`chip${graphUp ? ' teal' : ''}`}>
                  <span className="d" style={graphUp ? undefined : { background: 'var(--amber)' }} />
                  {graphUp ? 'knowledge graph live' : 'graph offline · RAG only'}
                </span>
                <span className="chip">
                  <span className="d" style={{ background: 'var(--mid)' }} />
                  {health.index?.documents ?? 0} drawings · v{health.index?.version ?? 0}
                </span>
              </>
            )}
            {!pathname.startsWith('/new') && (
              <button className="askbtn" onClick={() => navigate('/new')}>
                <span>Find a design</span>
                <svg viewBox="0 0 24 24" fill="none">
                  <path d="M5 12h14M13 6l6 6-6 6" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            )}
          </div>
        </header>
        <div className="wrap">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
