# substation_retrieval

Design reuse for AEML consumer substations (CSS). Describe a new site and get the closest historic CSS designs.
You can describe it as text, structured JSON, or a builder's site plan.

Each result comes with:

- a **reuse / adapt / new** verdict
- the checks that pass or fail
- the historic layout overlaid on the new site
- the original equipment layout, section and site plan from its drawing

The parts:

- **Extraction v2.** Each drawing (PDF; DXF/DWG first cut) becomes a schema-2.1 JSON. GPT-6 Astra makes two
  passes, then segmentation masks, scale calibration, section choice, catalogue rating inference and the
  plot-clearance check are applied.
- **Knowledge graph on JanusGraph (primary engine).** It is typed by the CSS ontology: 5 families
  (Site, Electrical, Civil, Access, Cable). Matching combines Hungarian vertex pairing, IDF edge consistency,
  the Aligned Layout Score and facet rarity.
- **RAG (fallback engine).** BM25 + feature vector + mask rasters (+ DINOv2 image embeddings when installed),
  re-ranked with the same geometry and compatibility checks.
- **Incremental ingestion.** One job per file (or a bulk folder), with index versioning. No full rebuild is needed.
- **UI** at http://127.0.0.1:8077 with five tabs:
  - Chat
  - Library & ingest
  - Ontology (tree)
  - Site graph (design tree / network + site summary + masks over the original drawing)
  - Admin

What has been implemented and verified is in the Claude Doc
[CSS Design Retrieval: First Draft Implementation](https://claude.ai/artifact/PexMPsfXUHQWNKMZ3tcvkS).

---

## 1. Prerequisites (Windows)

| Need | Install |
|---|---|
| Python ≥ 3.10 + uv | `winget install astral-sh.uv` (uv brings Python if missing) |
| Java 17 (for JanusGraph) | `winget install Microsoft.OpenJDK.17`, then open a **new** terminal |
| Astra key | one line in `C:\Users\ibpha\work\projects\.env` (any parent folder of the repo works): `ASTRA_API_KEY=...` (`OPENAI_API_KEY` also accepted; add `OPENAI_BASE_URL=...` if Astra is behind a gateway) |
| Drawings | PDFs in `..\..\data\all_data\css\pdf` (v1 parsed results in `...\pdf\css_parsed`) |

No docker is used anywhere.

## 2. First-time setup

Open PowerShell in the repo folder (`C:\Users\ibpha\work\projects\substation\code\substation_retrieval`):

```powershell
# a) Python environment
uv venv
uv pip install -r requirements.txt
uv pip install -r requirements-optional.txt     # ezdxf, anthropic, pytest
uv pip install -e .                             # installs the `sr` command into .venv

# b) JanusGraph (graph database): download once (~400 MB) and configure
powershell -ExecutionPolicy Bypass -File scripts\janusgraph\setup.ps1
```

## 3. Run (every time)

**Terminal 1: start JanusGraph** and leave the window open:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\janusgraph\start.ps1
```

**Terminal 2: the app.**

```powershell
.\.venv\Scripts\activate
sr janus-check        # JanusGraph reachable? creates the vid/pk indexes on first run
sr import-v1          # first run only: loads the 50 reviewed drawings into JanusGraph + RAG index (no LLM cost, ~20 s)
sr serve              # http://127.0.0.1:8077
```

`.\run.ps1` does the install, the check, the first-run import and `serve` in one go, once JanusGraph is running.

## 4. Extract drawings with Astra (real LLM extraction v2)

```powershell
sr health                                                  # "llm": {"available": true, "model": "gpt-6-astra"}
sr extract "..\..\data\all_data\css\pdf\CSS layout 10.02.25.pdf" --out var\test_v2.json   # one drawing, not indexed
sr extract-all --workers 4                                 # all PDFs in paths.data_dir, report vs v1
sr extract-all --workers 4 --index                         # same, and replace the v1 entries in the graph + index
```

What `extract-all` writes:

- `var\extractions\<doc_id>\doc.json` for each drawing
- debug artifacts in `var\artifacts\<doc_id>\`: page renders, layout crops, overlays, `masks\*.png`, raw LLM JSON
- a comparison CSV in `var\reports\extract_v2_<time>.csv`, covering substations, equipment, transformers,
  room L/W, clear height, scale source, section and kVA (v2 vs v1)

LLM answers are cached by file hash + prompt version, so re-runs only pay for what changed.

Adding new drawings later, one at a time or in bulk:

```powershell
sr ingest "D:\new_drawings\Site A.pdf" --no-v1 --wait   # --no-v1 forces Astra even if a v1 result exists
sr ingest "D:\new_drawings" --no-v1 --wait              # a whole folder
```

You can also use the UI: **Library & ingest → choose files → untick "reuse v1 extraction" → Upload & ingest**.
Each file is a job: extract → review gate → JanusGraph upsert → RAG upsert → new index version.

## 5. Using the UI

- **Chat.** Type a requirement, for example:
  *"indoor CSS on ground floor, 2 x 1500 kVA dry DT, 1 RMU, 4 LTP, room 11 x 8 m, clear height 4.5 m, rolling shutter, nalla 4 m away"*.
  - **Attach site plan** takes a PNG/JPG/PDF with the proposed CSS area marked in red. Blue = nalla,
    yellow = wayleave, green = trench. Enter the long side in mm for scale.
  - Each result card shows the score, verdict, checks and an **Overlay on new site**. The tabs on the card
    switch to the original **Equipment layout**, **Section**, **Site plan** and **Masks**.
  - Follow-up questions ("why is #2 lower?") are answered from the last results.
- **Site graph.** Pick a drawing.
  - **Design graph (tree)** is the original left-to-right view: click a group to fold it, click a box to see
    its vertex. **Network** is the force view.
  - The side panel lists the site summary.
  - Below it are the extracted geometry and **the drawing with the extracted masks**: toggle each class, or
    click a layout to zoom to it.
- **Ontology.** The class tree with instance counts; click a class for its fields and usage.
- **Admin.** Switch the KG / RAG engines on or off, set the retrieval mode (`auto` = KG then RAG fallback),
  and view health and index versions.

## 6. Optional: DINOv2 image embeddings (tiny variant)

```powershell
uv pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU-only torch (~200 MB)
uv pip install -r requirements-ml.txt                                   # transformers
sr setup-embedder        # saves facebook/dinov2-small (ViT-S/14, 22M params) to var\models
sr rebuild               # embeds every substation's mask raster (no LLM)
```

The setting is `engines.rag.image_embedder: dinov2`, and it is on by default. Without torch it stays inactive
and logs one line.

## 7. Configuration

`config\settings.yaml` holds all knobs. Environment variables override them:

| Setting | Env var | Default |
|---|---|---|
| graph backend | `SR_GRAPH_BACKEND` | `janusgraph` (`sqlite` = tests/offline only) |
| JanusGraph URL | `SR_GREMLIN_URL` | `ws://localhost:8182/gremlin` |
| retrieval mode | `SR_RETRIEVAL_MODE` | `auto` (`kg`, `rag`, `both`) |
| engines on/off | `CSS_KG_ENABLED`, `CSS_RAG_ENABLED` | `true` |
| LLM model / style | `SR_LLM_MODEL`, `SR_LLM_API_STYLE` | `gpt-6-astra`, `responses` (`chat` for gateways) |
| drawings / v1 / workspace | `SR_DATA_DIR`, `SR_V1_DIR`, `SR_VAR_DIR` | `..\..\data\all_data\css\pdf`, `...\css_parsed`, `.\var` |

The scoring weights, verdict thresholds (0.80 / 0.60), plot-clearance norms and catalogue tolerances are also in
`settings.yaml`.

## 8. Workspace (`var\`, gitignored)

| Path | Content |
|---|---|
| `var\janusgraph\` | JanusGraph install + its data (`data\berkeley`, `data\lucene`) |
| `var\db\index.sqlite` | document registry, RAG index (features, mask rasters, text, image embeddings), index versions |
| `var\db\jobs.sqlite` | ingest job queue |
| `var\extractions\<doc_id>\doc.json` | schema-2.1 extraction per drawing (source of truth for `sr rebuild`) |
| `var\artifacts\<doc_id>\` | page renders, crops, overlays, masks, raw LLM JSON |
| `var\cache\llm\`, `var\models\`, `var\reports\` | LLM cache, DINOv2 weights, extraction reports |

## 9. Commands

```
sr serve | import-v1 | ingest PATH... [--wait] [--no-v1] [--force] | extract FILE | extract-all [--workers N] [--index]
sr rebuild | search "TEXT" [--mode kg|rag|auto|both] | jobs | health | versions | janus-check | setup-embedder | worker
python -m pytest        # 18 tests, synthetic data, no network
```

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `JanusGraph at ws://localhost:8182 is not reachable` | start `scripts\janusgraph\start.ps1`; check `java -version` ≥ 11 |
| `HADOOP_HOME is not set` from JanusGraph | re-run `setup.ps1` (it downloads `winutils.exe`) |
| `"llm": {"available": false}` | the key line is missing in `.env`; check the name `ASTRA_API_KEY` or `OPENAI_API_KEY` |
| Astra 404 / connection error | set `OPENAI_BASE_URL` for your gateway, or `SR_LLM_API_STYLE=chat` |
| Search says "fallback to RAG" | expected when the query is thin, or JanusGraph is down; see the trace line |
| Masks / drawing views say "no image" | for v1 drawings, `SR_V1_DIR` must point at `css_parsed`; for v2, keep `var\artifacts` |

## Layout

```
src/substation_retrieval/
  core/        schema 2.1, ontology, equipment catalogue + rules, utils
  geometry/    polygons, ALS (alignment + set matching), compatibility checks, mask rasters, DINOv2 embedder
  extraction/  PDF/DXF/DWG adapters, Astra client + prompts, segmentation, scale, pipeline, batch, v1 importer
  kg/          partition builder, JanusGraph (Gremlin) + SQLite stores, KG matcher
  rag/         index (SQLite) + RAG engine
  retrieval/   query model + parser, site-plan encoder, shared scoring, orchestrator, overlays
  ingest/      job queue + workers, ingestion service
  chat/        chat agent
  api/         FastAPI app, tree views, drawing/mask images, static UI
scripts/janusgraph/   setup + start (Windows / Linux), schema.groovy, Bigtable config for GCP
```
