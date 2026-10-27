/* CSS Design Retrieval - single page UI (vanilla JS, no build step). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const FAMILY_COLOR = { site: "#0ea5b7", equipment: "#2563eb", civil: "#7c3aed", access: "#ea8a0c", cable: "#e11d48" };
const LABEL_COLOR = { document: "#111827", substation: "#7c3aed", layout: "#9ca3af", equipment: "#2563eb", opening: "#ea8a0c",
  structure: "#6d28d9", route: "#e11d48", site_feature: "#0ea5b7", facet: "#64748b", concept: "#a3a3a3", catalogue_item: "#15803d" };
const GROUP_COLOR = { equipment: "#2563eb", door: "#ea8a0c", louvre: "#ca8a04", cutout: "#e11d48", column: "#6d28d9", beam: "#7c3aed",
  trench: "#059669", cable_tray: "#0d9488" };

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(body.detail || body || r.statusText);
  return body;
}
const postJSON = (path, data, method = "POST") => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

/* ---------------------------------------------------------------- tabs */
const loaded = {};
function showTab(name) {
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  if (name === "library") refreshLibrary();
  if (name === "ontology" && !loaded.onto) { loaded.onto = true; loadOntology(); }
  if (name === "site") loadSiteList();
  if (name === "admin") loadAdmin();
}
$$("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

async function refreshStatus() {
  try {
    const h = await api("/api/health");
    const e = h.engines.flags;
    $("#status").textContent = `${h.index.documents} drawings · ${h.index.substations} substations · index v${h.index.version} · mode ${e.retrieval_mode}` +
      ` · KG ${e.kg_enabled ? (h.engines.kg.ok ? "on" : "down") : "off"} · RAG ${e.rag_enabled ? "on" : "off"} · LLM ${h.llm.available ? h.llm.model : "not configured"}`;
  } catch (err) { $("#status").textContent = "server unreachable"; }
}

/* ---------------------------------------------------------------- chat */
const chat = { session: null, siteplan: null };

function addMsg(role, html) {
  const d = document.createElement("div");
  d.className = "msg " + role;
  d.innerHTML = html;
  $("#messages").appendChild(d);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  return d;
}

async function send() {
  const text = $("#input").value.trim();
  if (!text) return;
  $("#input").value = "";
  addMsg("user", esc(text));
  const wait = addMsg("bot", '<span class="muted">matching…</span>');
  $("#send").disabled = true;
  try {
    const out = await postJSON("/api/chat", { message: text, session_id: chat.session, siteplan_id: chat.siteplan?.id || null,
      mode: $("#mode").value || null, k: 6 });
    chat.session = out.session_id;
    wait.innerHTML = esc(out.answer) + (out.search ? renderSearch(out.search) : "");
    bindResultActions(wait);
  } catch (err) {
    wait.innerHTML = `<span class="tag fail">error</span> ${esc(err.message)}`;
  }
  $("#send").disabled = false;
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

function renderSearch(s) {
  const asked = (s.asked || []).map((a) => `<span class="tag">${esc(a)}</span>`).join("");
  const feas = (s.feasibility || []).map((c) => `<span class="tag ${c.status === "fail" ? "fail" : ""}">${esc(c.detail)}</span>`).join("");
  const trace = (s.trace || []).map((t) => `<div class="meta">↳ ${esc(t)}</div>`).join("");
  const engine = { kg: "Ontology KG matching", rag: "RAG matching", both: "KG + RAG merged" }[s.engine] || "no engine";
  const cards = (s.results || []).map((r, i) => resultCard(r, i)).join("");
  return `<div class="meta">${esc(engine)} · ${s.elapsed_ms} ms</div>${trace}<div class="asked">${asked}${feas}</div><div class="results">${cards || '<span class="muted">no matches</span>'}</div>`;
}

function resultCard(r, i) {
  const comps = Object.entries(r.components || {}).map(([k, v]) => `<span class="tag">${esc(k)} ${(+v).toFixed(2)}</span>`).join("");
  const checks = (r.checks || []).map((c) => `<li class="${c.status}">${esc(c.detail)}</li>`).join("");
  const expl = (r.explanation || []).slice(0, 5).map((e) => `<div class="small muted">${esc(e)}</div>`).join("");
  return `<div class="res">
    <div class="head"><span class="rank">#${i + 1}</span><span class="title">${esc(r.file_name)}</span></div>
    <div class="row small"><span class="pill ${r.verdict}">${r.verdict}</span><b>${(+r.score).toFixed(3)}</b>
      <span class="muted">${esc(r.substation_name)} · ${r.engine.toUpperCase()} · coverage ${(100 * r.coverage).toFixed(0)}%${r.status ? " · " + esc(r.status) : ""}</span></div>
    <div class="bar"><i style="width:${Math.min(100, 100 * r.score)}%"></i></div>
    ${r.overlay ? overlaySVG(r.overlay) : ""}
    <div class="comps">${comps}</div>
    <ul class="checks">${checks}</ul>${expl}
    <div class="actions"><button class="btn" data-graph="${esc(r.doc_id)}">Site graph</button>
      <button class="btn" data-json="${esc(r.doc_id)}">Extraction JSON</button></div></div>`;
}

function bindResultActions(root) {
  $$("[data-graph]", root).forEach((b) => b.addEventListener("click", () => { showTab("site"); selectSite(b.dataset.graph); }));
  $$("[data-json]", root).forEach((b) => b.addEventListener("click", () => window.open("/api/documents/" + b.dataset.json, "_blank")));
}

/* polygons in mm, y down */
function bounds(polys) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  polys.forEach((p) => (p || []).forEach(([x, y]) => { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }));
  return isFinite(x0) ? [x0, y0, x1, y1] : [0, 0, 1000, 1000];
}
const pts = (p) => (p || []).map(([x, y]) => `${x.toFixed(0)},${y.toFixed(0)}`).join(" ");

function overlaySVG(o) {
  const all = [o.query_outline, o.room, ...(o.items || []).map((i) => i.coords), ...(o.query_items || []).map((i) => i.coords)];
  const [x0, y0, x1, y1] = bounds(all);
  const pad = 0.06 * Math.max(x1 - x0, y1 - y0);
  const sw = Math.max(x1 - x0, y1 - y0) / 220;
  let s = `<svg class="overlay" viewBox="${x0 - pad} ${y0 - pad} ${x1 - x0 + 2 * pad} ${y1 - y0 + 2 * pad}">`;
  (o.site_features || []).forEach((f) => {
    if (!f.coords) return;
    s += f.line ? `<polyline points="${pts(f.coords)}" fill="none" stroke="#0ea5b7" stroke-width="${sw * 2}"/>` :
      `<polygon points="${pts(f.coords)}" fill="#0ea5b733" stroke="#0ea5b7" stroke-width="${sw}"/>`;
  });
  if (o.query_outline) s += `<polygon points="${pts(o.query_outline)}" fill="none" stroke="#111827" stroke-dasharray="${sw * 4}" stroke-width="${sw * 1.5}"/>`;
  if (o.room) s += `<polygon points="${pts(o.room)}" fill="#7c3aed14" stroke="#7c3aed" stroke-width="${sw * 1.4}"/>`;
  (o.query_items || []).forEach((i) => { s += `<polygon points="${pts(i.coords)}" fill="#11182733" stroke="#111827" stroke-width="${sw}"/>`; });
  (o.items || []).forEach((i) => {
    const c = GROUP_COLOR[i.group] || "#64748b";
    s += (i.group === "trench" || i.group === "cable_tray") && i.coords.length < 3 ?
      `<polyline points="${pts(i.coords)}" fill="none" stroke="${c}" stroke-width="${sw * 2}"/>` :
      `<polygon points="${pts(i.coords)}" fill="${c}33" stroke="${c}" stroke-width="${sw}"><title>${esc(i.label || i.cls)}</title></polygon>`;
  });
  return s + "</svg>";
}

$("#send").addEventListener("click", send);
$("#input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
$$(".ex").forEach((b) => b.addEventListener("click", () => { $("#input").value = b.textContent; send(); }));

$("#siteplan-file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  const L = $("#sp-length").value;
  if (L) fd.append("known_length_mm", L);
  const chip = $("#attach-chip");
  chip.classList.remove("hidden");
  chip.innerHTML = `analysing ${esc(f.name)}…`;
  try {
    const r = await api("/api/siteplan", { method: "POST", body: fd });
    chat.siteplan = r;
    const a = r.annotated_area;
    chip.innerHTML = (r.preview_url ? `<img src="${r.preview_url}">` : "") +
      `<span>${esc(f.name)}: ${a ? esc(a.shape) + (a.length_mm ? ` ${(a.length_mm / 1000).toFixed(2)} × ${(a.width_mm / 1000).toFixed(2)} m` : " (no scale: enter long side mm and re-attach)") : esc(r.note || r.error)}` +
      `${r.features?.length ? ` · ${r.features.length} site feature(s)` : ""}</span><button title="remove">✕</button>`;
    $("button", chip).addEventListener("click", () => { chat.siteplan = null; chip.classList.add("hidden"); });
  } catch (err) { chip.innerHTML = `site plan failed: ${esc(err.message)}`; }
  e.target.value = "";
});

/* ---------------------------------------------------------------- library */
let jobTimer = null;
async function refreshLibrary() {
  const [docs, jobs] = await Promise.all([api("/api/documents"), api("/api/jobs?limit=100")]);
  renderDocs(docs);
  renderJobs(jobs);
  const active = jobs.some((j) => j.status === "queued" || j.status === "extracting");
  clearTimeout(jobTimer);
  if (active) jobTimer = setTimeout(refreshLibrary, 2500);
}

function renderJobs(jobs) {
  if (!jobs.length) { $("#jobs").innerHTML = '<span class="muted">no jobs yet</span>'; return; }
  $("#jobs").innerHTML = `<table><tr><th>file</th><th>status</th><th>stage</th><th>progress</th><th>message</th></tr>` +
    jobs.map((j) => `<tr><td>${esc(j.file_name)}</td><td><span class="st ${j.status}">${j.status}</span></td><td class="small">${esc(j.stage)}</td>
      <td><progress class="prog" max="1" value="${j.progress || 0}"></progress></td><td class="small">${esc(j.error || j.message || "")}</td></tr>`).join("") + "</table>";
}

let DOCS = [];
function renderDocs(docs) {
  DOCS = docs;
  const f = ($("#doc-filter").value || "").toLowerCase();
  const rows = docs.filter((d) => !f || d.file_name.toLowerCase().includes(f));
  $("#doc-count").textContent = `${rows.length} of ${docs.length}`;
  $("#docs").innerHTML = `<table><tr><th>drawing</th><th>status</th><th class="num">quality</th><th class="num">subs</th><th>method</th><th class="num">ver</th><th></th></tr>` +
    rows.map((d) => `<tr><td>${esc(d.file_name)}</td><td><span class="st ${d.status}">${d.status}</span></td><td class="num">${d.quality ?? ""}</td>
      <td class="num">${d.n_substations}</td><td class="small">${esc(d.method)}</td><td class="num">${d.version ?? ""}</td>
      <td><button class="btn" data-site="${d.doc_id}">view</button>
      ${d.status !== "reviewed" ? `<button class="btn" data-approve="${d.doc_id}">approve</button>` : ""}
      <button class="btn" data-del="${d.doc_id}">delete</button></td></tr>`).join("") + "</table>";
  $$("[data-site]").forEach((b) => b.addEventListener("click", () => { showTab("site"); selectSite(b.dataset.site); }));
  $$("[data-approve]").forEach((b) => b.addEventListener("click", async () => { await api(`/api/documents/${b.dataset.approve}/approve`, { method: "POST" }); refreshLibrary(); }));
  $$("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Remove this drawing from the graph and index?")) return;
    await api(`/api/documents/${b.dataset.del}`, { method: "DELETE" }); refreshLibrary(); refreshStatus();
  }));
}
$("#doc-filter").addEventListener("input", () => renderDocs(DOCS));

$("#ingest-btn").addEventListener("click", async () => {
  const files = $("#ingest-files").files;
  if (!files.length) return alert("Choose PDF / DXF / DWG files first");
  const fd = new FormData();
  [...files].forEach((f) => fd.append("files", f));
  fd.append("use_v1", $("#opt-v1").checked);
  fd.append("force", $("#opt-force").checked);
  if ($("#opt-gate").value) fd.append("review_gate", $("#opt-gate").value);
  $("#ingest-btn").disabled = true;
  try { await api("/api/ingest", { method: "POST", body: fd }); } catch (err) { alert(err.message); }
  $("#ingest-btn").disabled = false;
  $("#ingest-files").value = "";
  refreshLibrary();
});
$("#import-v1").addEventListener("click", async () => {
  $("#import-v1").disabled = true;
  try { const r = await api("/api/admin/import-v1", { method: "POST" }); alert(`imported ${r.imported}, failed ${r.failed.length}, version ${r.version}`); }
  catch (err) { alert(err.message); }
  $("#import-v1").disabled = false; refreshLibrary(); refreshStatus();
});
$("#rebuild").addEventListener("click", async () => {
  $("#rebuild").disabled = true;
  try { const r = await api("/api/admin/rebuild", { method: "POST" }); alert(`re-indexed ${r.reindexed}, version ${r.version}`); }
  catch (err) { alert(err.message); }
  $("#rebuild").disabled = false; refreshLibrary(); refreshStatus();
});

/* ---------------------------------------------------------------- force layout */
function forceLayout(nodes, edges, W, H, iters = 320) {
  const idx = new Map(nodes.map((n, i) => [n.id, i]));
  nodes.forEach((n, i) => { n.x = W / 2 + Math.cos(i) * W * 0.3 * Math.random(); n.y = H / 2 + Math.sin(i) * H * 0.3 * Math.random(); n.vx = 0; n.vy = 0; });
  const E = edges.map((e) => [idx.get(e.out), idx.get(e.in)]).filter(([a, b]) => a !== undefined && b !== undefined);
  const k = Math.sqrt((W * H) / Math.max(nodes.length, 1)) * 0.55;
  for (let t = 0; t < iters; t++) {
    const temp = 1 - t / iters;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy + 0.01;
        const f = (k * k) / d2;
        a.vx += dx * f * 0.05; a.vy += dy * f * 0.05; b.vx -= dx * f * 0.05; b.vy -= dy * f * 0.05;
      }
    }
    E.forEach(([i, j]) => {
      const a = nodes[i], b = nodes[j];
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) + 0.01;
      const f = (d - k) / d * 0.08;
      a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
    });
    nodes.forEach((n) => {
      n.vx += (W / 2 - n.x) * 0.002; n.vy += (H / 2 - n.y) * 0.002;
      const v = Math.sqrt(n.vx * n.vx + n.vy * n.vy), cap = 12 * temp + 0.5;
      if (v > cap) { n.vx *= cap / v; n.vy *= cap / v; }
      n.x = Math.max(20, Math.min(W - 20, n.x + n.vx)); n.y = Math.max(20, Math.min(H - 20, n.y + n.vy));
      n.vx *= 0.6; n.vy *= 0.6;
    });
  }
}

function drawGraph(svg, nodes, edges, colorOf, onClick) {
  const W = svg.clientWidth || 700, H = svg.clientHeight || 520;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  forceLayout(nodes, edges, W, H);
  const pos = new Map(nodes.map((n) => [n.id, n]));
  let s = "";
  edges.forEach((e) => {
    const a = pos.get(e.out), b = pos.get(e.in);
    if (!a || !b) return;
    s += `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#94a3b8" stroke-opacity=".55"><title>${esc(e.label)}</title></line>`;
    if (edges.length < 160) s += `<text x="${(a.x + b.x) / 2}" y="${(a.y + b.y) / 2}" fill="#94a3b8" style="font-size:8px">${esc(e.label)}</text>`;
  });
  nodes.forEach((n, i) => {
    const r = n.label === "substation" || n.label === "document" ? 9 : n.label === "concept" ? 5 : 6;
    s += `<g data-i="${i}" style="cursor:pointer"><circle cx="${n.x}" cy="${n.y}" r="${r}" fill="${colorOf(n)}" stroke="#fff" stroke-width="1.2"><title>${esc(n.label)}: ${esc(n.name)}</title></circle>
      <text x="${n.x + r + 2}" y="${n.y + 3}">${esc(String(n.name).slice(0, 22))}</text></g>`;
  });
  svg.innerHTML = s;
  $$("g[data-i]", svg).forEach((g) => g.addEventListener("click", () => onClick && onClick(nodes[+g.dataset.i])));
}

/* ---------------------------------------------------------------- ontology */
async function loadOntology() {
  const o = await api("/api/ontology");
  const tree = (n, fam) => {
    const color = n.color || fam || "#94a3b8";
    return `<li><span class="node"><span class="dot" style="background:${color}"></span><b>${esc(n.label)}</b>
      <span class="cnt">${n.count ?? 0}</span>${n.vertex ? `<span class="fields">${esc(n.vertex)}${n.fields.length ? " · " + esc(n.fields.join(", ")) : ""}</span>` : ""}</span>
      ${n.children.length ? "<ul>" + n.children.map((c) => tree(c, color)).join("") + "</ul>" : ""}</li>`;
  };
  $("#onto-tree").innerHTML = `<div class="tree"><ul>${tree(o.tree)}</ul></div>`;
  const nodes = [], edges = [];
  const walk = (n, parent, fam) => {
    const color = n.color || fam || "#64748b";
    nodes.push({ id: n.id, name: `${n.label}${n.count ? " (" + n.count + ")" : ""}`, label: "concept", color });
    if (parent) edges.push({ out: n.id, in: parent, label: "SUBCLASS_OF" });
    n.children.forEach((c) => walk(c, n.id, color));
  };
  walk(o.tree, null, null);
  drawGraph($("#onto-svg"), nodes, edges, (n) => n.color);
  $("#onto-meta").textContent = `vertex labels: ${o.vertex_labels.join(", ")} · edge labels: ${o.edge_labels.join(", ")}`;
}

/* ---------------------------------------------------------------- site graph */
async function loadSiteList() {
  const docs = DOCS.length ? DOCS : await api("/api/documents");
  DOCS = docs;
  const sel = $("#site-doc");
  const cur = sel.value;
  sel.innerHTML = docs.map((d) => `<option value="${d.doc_id}">${esc(d.file_name)}</option>`).join("");
  if (cur) sel.value = cur;
  if (sel.value && !loaded.site) { loaded.site = true; selectSite(sel.value); }
}
$("#site-doc").addEventListener("change", (e) => selectSite(e.target.value));
$("#hide-concepts").addEventListener("change", () => selectSite($("#site-doc").value));
$("#hide-layouts").addEventListener("change", () => selectSite($("#site-doc").value));

async function selectSite(docId) {
  if (!docId) return;
  if ($("#site-doc").value !== docId) {
    if (!DOCS.length) await loadSiteList();
    $("#site-doc").value = docId;
  }
  const [g, geo] = await Promise.all([api(`/api/documents/${docId}/graph`), api(`/api/documents/${docId}/geometry`)]);
  let nodes = g.nodes, edges = g.edges;
  if ($("#hide-concepts").checked) nodes = nodes.filter((n) => n.label !== "concept");
  if ($("#hide-layouts").checked) nodes = nodes.filter((n) => n.label !== "layout");
  const keep = new Set(nodes.map((n) => n.id));
  edges = edges.filter((e) => keep.has(e.out) && keep.has(e.in));
  $("#site-meta").textContent = `${nodes.length} vertices · ${edges.length} edges · quality ${geo.quality.score} (${geo.quality.status})`;
  drawGraph($("#site-svg"), nodes, edges, (n) => (n.family && FAMILY_COLOR[n.family]) || LABEL_COLOR[n.label] || "#64748b",
    (n) => { $("#node-info").innerHTML = `<b>${esc(n.label)}</b> ${esc(n.id)}<pre>${esc(JSON.stringify(n.props, null, 1))}</pre>`; });
  $("#site-geom").innerHTML = geo.substations.map((s) => `<div class="card"><b>${esc(s.name)}</b> <span class="muted small">floor ${esc(s.floor || "-")} · section ${esc(s.section || "-")}</span>
     ${s.room ? overlaySVG({ room: s.room, items: s.items }) : '<div class="muted">no room geometry</div>'}
     <div class="small">${Object.entries(s.civil).filter(([k, v]) => v != null).map(([k, v]) => `<span class="tag">${esc(k)} ${esc(v)}</span>`).join(" ")}</div>
     <table><tr><th>label</th><th>type</th><th class="num">kVA</th><th>source</th><th class="num">L×B×H mm</th></tr>${s.equipment.map((e) =>
       `<tr><td>${esc(e.label)}</td><td>${esc(e.equipment_type)}</td><td class="num">${e.kva ?? ""}</td><td class="small">${esc(e.kva_source || "")}</td>
        <td class="num">${[e.L_mm, e.B_mm, e.H_mm].map((v) => (v ? Math.round(v) : "–")).join("×")}</td></tr>`).join("")}</table>
     ${s.section_reason ? `<div class="small muted">section: ${esc(s.section_reason)}</div>` : ""}</div>`).join("") +
    `<div class="small">${(geo.site?.clearance_checks || []).map((c) => `<span class="tag ${c.meets_norm ? "" : "fail"}">${esc(c.feature)} ${(c.distance_mm / 1000).toFixed(1)} m / ${(c.norm_mm / 1000).toFixed(1)} m</span>`).join(" ")}</div>`;
  const imgs = (geo.artifacts || []).filter((a) => /\.(jpe?g|png)$/i.test(a));
  $("#site-art").innerHTML = imgs.length ? imgs.map((a) => `<a href="/api/artifacts/${docId}/${a}" target="_blank"><img loading="lazy" src="/api/artifacts/${docId}/${a}">${esc(a)}</a>`).join("")
    : '<span class="muted small">no debug artifacts (imported from v1, or debug saving off)</span>';
}

/* ---------------------------------------------------------------- admin */
async function loadAdmin() {
  const [h, f, v, s] = await Promise.all([api("/api/health"), api("/api/engines"), api("/api/versions"), api("/api/settings")]);
  $("#kg-on").checked = f.kg_enabled; $("#rag-on").checked = f.rag_enabled; $("#admin-mode").value = f.retrieval_mode;
  $("#health").textContent = JSON.stringify(h, null, 1);
  $("#settings").textContent = JSON.stringify(s, null, 1);
  $("#versions").innerHTML = `<table><tr><th class="num">v</th><th>created</th><th>note</th><th class="num">docs</th></tr>` +
    v.map((x) => `<tr><td class="num">${x.version}</td><td class="small">${esc(x.created_at)}</td><td>${esc(x.note)}</td><td class="num">${x.n_docs}</td></tr>`).join("") + "</table>";
}
$("#save-engines").addEventListener("click", async () => {
  await postJSON("/api/engines", { retrieval_mode: $("#admin-mode").value, kg_enabled: $("#kg-on").checked, rag_enabled: $("#rag-on").checked }, "PUT");
  loadAdmin(); refreshStatus();
});

refreshStatus();
setInterval(refreshStatus, 15000);
