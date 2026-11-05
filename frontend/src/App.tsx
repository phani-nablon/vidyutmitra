import type { ReactElement } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Shell } from './components/Shell';
import { Spinner } from './components/ui';
import { useAuth } from './lib/auth';
import { Home } from './pages/Home';
import { Login } from './pages/Login';
import { NewDesign } from './pages/NewDesign';
import { ReviewQueue } from './pages/ReviewQueue';
import { ReviewDrawing } from './pages/ReviewDrawing';

function RequireAuth({ children }: { children: ReactElement }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <Spinner label="Checking your session" />;
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  return children;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <RequireAuth>
            <Shell />
          </RequireAuth>
        }
      >
        <Route index element={<Home />} />
        <Route path="new" element={<NewDesign />} />
        <Route path="review" element={<ReviewQueue />} />
        <Route path="review/:docId" element={<ReviewDrawing />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
