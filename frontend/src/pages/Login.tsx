import { useState, type FormEvent } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import { Alert, BrandMark, Field } from '../components/ui';

/** Sign-in screen, from the VidyutMitra platform. Entra ID SSO would replace this screen, not the roles. */
export function Login() {
  const { user, signIn } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get('next') || '/';
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // /classic is served by the backend, not this app, so it needs a real page load
  const go = (to: string) => (to.startsWith('/classic') ? location.assign(to) : navigate(to, { replace: true }));

  if (user && !next.startsWith('/classic')) return <Navigate to={next} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await signIn(username, password);
      go(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not sign in');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login viz-dot-grid">
      <div className="login-col">
        <div className="brand">
          <BrandMark />
          <div>
            <div className="bt">Nablon SLIDe</div>
            <div className="bs">Substation design retrieval</div>
          </div>
        </div>

        <section className="card glass">
          <div className="cbody login-body">
            <div className="top">
              <span className="orb sm" />
              <p className="eyebrow">Sign in</p>
            </div>
            <p className="hl">
              <b>Reuse what has already been built.</b> Describe a new site and get the closest past CSS designs, or
              check what was read off a drawing and correct it.
            </p>

            <form className="stack" onSubmit={submit}>
              {error && <Alert tone="error">{error}</Alert>}
              <Field label="Login ID">
                <input className="input" value={username} autoFocus autoComplete="username" onChange={(e) => setUsername(e.target.value)} />
              </Field>
              <Field label="Password">
                <input
                  className="input"
                  type="password"
                  value={password}
                  autoComplete="current-password"
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>
              <button className="btn primary block" disabled={busy || !username || !password}>
                {busy ? 'Signing in…' : 'Sign in'}
              </button>
              <p className="small muted">No account? Ask an admin to add you.</p>
            </form>
          </div>

        </section>
        <p className="mono small muted" style={{ textAlign: 'center' }}>
          AEML · CSS library · username and password sign-in
        </p>
      </div>
    </div>
  );
}
