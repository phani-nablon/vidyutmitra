import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from './api';
import type { Role, User } from './types';

/**
 * Role capabilities, mirrored from api/auth.py (required_role). The UI hides what a role cannot do;
 * the API is what actually stops it.
 */
const CAN = {
  search: ['viewer', 'editor', 'admin'],
  edit: ['editor', 'admin'],
  admin: ['admin'],
} satisfies Record<string, Role[]>;

export type Permission = keyof typeof CAN;

export const ROLE_LABEL: Record<Role, string> = {
  admin: 'Admin',
  editor: 'Editor · Reviewer',
  viewer: 'Viewer',
};

interface AuthValue {
  user: User | null;
  authOn: boolean;
  loading: boolean;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  can: (p: Permission) => boolean;
}

const Ctx = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [authOn, setAuthOn] = useState(true);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .me()
      .then((r) => {
        setUser(r.user);
        setAuthOn(r.auth);
      })
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const signIn = useCallback(async (username: string, password: string) => {
    const r = await api.login(username, password);
    setUser(r.user);
  }, []);

  const signOut = useCallback(async () => {
    await api.logout().catch(() => undefined);
    setUser(null);
  }, []);

  const can = useCallback((p: Permission) => !!user && (CAN[p] as Role[]).includes(user.role), [user]);

  const value = useMemo(() => ({ user, authOn, loading, signIn, signOut, can }), [user, authOn, loading, signIn, signOut, can]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}
