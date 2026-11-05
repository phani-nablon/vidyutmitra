/** Shapes returned by the FastAPI backend (src/substation_retrieval/api). Only what the UI reads. */

export interface Health {
  ok: boolean;
  version: string;
  index: { documents: number; substations: number; by_status: Record<string, number>; version: number };
  engines: { flags: Record<string, unknown>; kg?: { ok?: boolean; enabled?: boolean }; rag?: { ok?: boolean } };
  jobs: Record<string, number>;
  llm: { available?: boolean };
}

export interface EquipmentRow {
  type: string;
  count: number;
  kva?: number | null;
  dt_kind?: string | null;
  ways?: number | null;
}

/** The structured query the builder sends; every field is optional (only stated fields are scored). */
export interface StructuredQuery {
  installation?: string;
  context?: string;
  floor?: string;
  arrangement?: string;
  voltage_kv?: number;
  equipment_items?: EquipmentRow[];
  length_mm?: number;
  width_mm?: number;
  area_m2?: number;
  clear_height_mm?: number;
  beam_bottom_mm?: number;
  door_width_mm?: number;
  shape?: string;
  platform?: boolean;
  civil?: Record<string, boolean | number>;
  site_distances?: Record<string, number>;
}

export interface Check {
  id: string;
  status: 'pass' | 'fail' | 'warn' | 'na' | string;
  detail: string;
}

export interface Overlay {
  query_outline: number[][] | null;
  room: number[][] | null;
  items: Array<{ group: string; cls: string; label?: string; coords: number[][] | number[][][] }>;
  query_items: Array<{ group: string; cls: string; coords: number[][] }>;
  name?: string;
  clear_height_mm?: number;
  equipment_layout_id?: string;
  section_layout_id?: string;
  site_layout_id?: string;
  page: number;
  error?: string;
}

export interface SearchResult {
  engine: string;
  doc_id: string;
  substation_id: string;
  file_name: string;
  substation_name: string;
  score: number;
  verdict: 'reuse' | 'adapt' | 'new' | string;
  coverage: number;
  components: Record<string, number>;
  checks: Check[];
  conflicts: string[];
  explanation: string[];
  status?: string | null;
  overlay?: Overlay;
}

export interface SearchResponse {
  engine: string;
  mode: string;
  results: SearchResult[];
  feasibility: Check[];
  asked: string[];
  query: Record<string, unknown>;
  info?: unknown;
  elapsed_ms: number;
}

export interface ChatResponse {
  session_id: string;
  answer: string;
  intent: string;
  search: SearchResponse | null;
}

export interface SitePlan {
  id: string;
  preview_url?: string;
  area_m2?: number;
  error?: string;
  [k: string]: unknown;
}

export interface Action {
  kind: 'gate' | 'unreviewed' | 'issue' | 'missing' | 'clearance' | 'flagged' | string;
  text: string;
  item_id?: string;
}

export interface QueueRow {
  doc_id: string;
  file_name: string;
  status: string | null;
  score: number | null;
  open_actions: number;
  actions: Action[];
  installation?: string | null;
  floor?: string | null;
  n_substations: number;
  n_equipment: number;
  reviewed_at?: string | null;
  updated_at?: string | null;
}

export interface ReviewValue {
  name: string;
  type: string;
  unit: string | null;
  summary: string;
  value?: string | number | boolean;
  help?: string;
  note?: string;
  editable?: boolean;
}

export interface Loc {
  page: number;
  box: number[] | null;
  polygon: number[][] | null;
  precision: 'exact' | 'segment' | 'layout' | 'none';
  layout_id?: string | null;
  why?: string | null;
}

export interface Mark {
  verdict: 'correct' | 'wrong' | 'unsure' | null;
  note: string;
  at: string;
}

export interface ReviewItem {
  id: string;
  kind: string;
  label: string;
  class: string | null;
  class_label: string | null;
  values: ReviewValue[];
  missing: Array<{ name: string; type: string; unit: string; help: string }>;
  loc: Loc;
  mark?: Mark | null;
}

export interface ReviewGroup {
  id: string;
  label: string;
  items: ReviewItem[];
}

export interface Clearance {
  id: string;
  kind: string;
  status: 'pass' | 'fail' | 'info';
  label: string;
  values: ReviewValue[];
  loc: Loc;
}

export interface ReviewDoc {
  doc_id: string;
  file_name: string;
  status: string | null;
  score: number | null;
  issues: string[];
  actions: Action[];
  pages: Array<{ page: number; w: number | null; h: number | null; has_image: boolean }>;
  groups: ReviewGroup[];
  clearances: Clearance[];
  layouts: Array<{ id: string; type: string; title: string; page: number; box: number[] }>;
  metadata: Record<string, unknown>;
  edits: Array<{ item_id: string; name: string; old: unknown; new: unknown; at: string }>;
  marks: Record<string, Mark>;
}

export interface Edit {
  item_id: string;
  name: string;
  value: string | number | boolean | null;
}

export type Role = 'viewer' | 'editor' | 'admin';

export interface User {
  username: string;
  name: string;
  role: Role;
}
