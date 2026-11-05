import type {
  ChatResponse,
  Edit,
  Health,
  Mark,
  User,
  QueueRow,
  ReviewDoc,
  SearchResponse,
  SitePlan,
  StructuredQuery,
} from './types';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const res = await fetch(`/api${path}`, { ...init, headers, credentials: 'same-origin' });
  // session gone (expired, signed out elsewhere): back to the sign-in page, then here again
  if (res.status === 401 && !path.startsWith('/auth/') && !location.pathname.startsWith('/login')) {
    location.assign(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown })?.detail;
    throw new ApiError(typeof detail === 'string' ? detail : res.statusText, res.status);
  }
  return data as T;
}

const json = (value: unknown) => JSON.stringify(value);

export const api = {
  health: () => request<Health>('/health'),

  // ---- sign-in
  authMode: () => request<{ enabled: boolean; dev_accounts: boolean }>('/auth/mode'),
  me: () => request<{ user: User; auth: boolean }>('/auth/me'),
  login: (username: string, password: string) =>
    request<{ user: User }>('/auth/login', { method: 'POST', body: json({ username, password }) }),
  logout: () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' }),

  // ---- page 1: create new
  search: (text: string, structured: StructuredQuery | null, siteplanId?: string | null, k = 8) =>
    request<SearchResponse>('/search', {
      method: 'POST',
      body: json({ text, structured, siteplan_id: siteplanId ?? null, k, overlays: true }),
    }),
  chat: (message: string, sessionId?: string | null, siteplanId?: string | null, k = 6) =>
    request<ChatResponse>('/chat', {
      method: 'POST',
      body: json({ message, session_id: sessionId ?? null, siteplan_id: siteplanId ?? null, k }),
    }),
  siteplan: (file: File, color: string, knownLengthMm?: number | null) => {
    const form = new FormData();
    form.append('file', file);
    form.append('color', color);
    if (knownLengthMm) form.append('known_length_mm', String(knownLengthMm));
    return request<SitePlan>('/siteplan', { method: 'POST', body: form });
  },

  // ---- page 2: review
  queue: () => request<{ documents: QueueRow[]; total_open: number }>('/workbench/queue'),
  review: (docId: string) => request<ReviewDoc>(`/workbench/documents/${encodeURIComponent(docId)}`),
  mark: (docId: string, itemId: string, verdict: Mark['verdict'], note = '') =>
    request<Record<string, Mark>>(`/workbench/documents/${encodeURIComponent(docId)}/marks`, {
      method: 'PUT',
      body: json({ item_id: itemId, verdict, note }),
    }),
  saveEdits: (docId: string, edits: Edit[]) =>
    request<{ changed: unknown[] }>(`/workbench/documents/${encodeURIComponent(docId)}/fields`, {
      method: 'PATCH',
      body: json({ edits }),
    }),
  submit: (docId: string, edits: Edit[]) =>
    request<{ version: number; changed: unknown[] }>(`/workbench/documents/${encodeURIComponent(docId)}/submit`, {
      method: 'POST',
      body: json({ edits }),
    }),
};

/** Image URLs served by the backend (page renders, layout crops). */
export const img = {
  page: (docId: string, page = 1) => `/api/documents/${encodeURIComponent(docId)}/page/${page}.jpg`,
  layout: (docId: string, layoutId: string) =>
    `/api/documents/${encodeURIComponent(docId)}/layout/${encodeURIComponent(layoutId)}.jpg`,
};
