# substation_retrieval

Design reuse for AEML consumer substations (CSS). Describe a new site and get the closest historic CSS designs.
You can describe it as text, structured JSON, or a builder's site plan.

Each result comes with:

- a **reuse / adapt / new** verdict
- the checks that pass or fail
- the historic layout overlaid on the new site
- the original equipment layout, section and site plan from its drawing

The parts:

- **Extraction v2.** Each drawing (PDF; DXF/DWG first cut) becomes a schema-2.1 JSON. The LLM makes two
  passes, then segmentation masks, scale calibration, section choice, catalogue rating inference and the
  plot-clearance check are applied.
- **Knowledge graph on Neo4j (primary engine).** It is typed by the CSS ontology: 5 families
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
| Java 17 or 21 (for Neo4j) | nothing to do: if Java is missing or older than 17, `sr neo4j-start` downloads a portable Java 17 runtime into `var\jre` (~46 MB, no admin rights). An installed JDK (`winget install Microsoft.OpenJDK.17`) is used when present |
| LLM key | one line in `C:\Users\ibpha\work\projects\.env` (any parent folder of the repo works): `OPENAI_API_KEY=...` (`ASTRA_API_KEY` also accepted; add `OPENAI_BASE_URL=...` if the LLM is behind a gateway) |
| Drawings | PDFs in `..\..\data\all_data\css\pdf` (v1 parsed results in `...\pdf\css_parsed`) |

No docker is used anywhere.

## 2. First-time setup

Open PowerShell in the repo folder (`C:\Users\ibpha\work\projects\substation\code\substation_retrieval`):

```powershell
uv venv
uv pip install -r requirements.txt
uv pip install -r requirements-optional.txt     # ezdxf, anthropic, pytest
uv pip install -e .                             # installs the `sr` command into .venv
.\.venv\Scripts\activate
sr neo4j-start      # checks Java, downloads Neo4j Community once (~130 MB) into var\neo4j, sets the password, starts it in the background
sr neo4j-check      # graph reachable, constraint and index created
sr import-v1        # loads the 50 reviewed drawings into Neo4j + the RAG index (no LLM cost, ~20 s)
```

`sr` is this repo's command-line tool (it is installed into `.venv`; activate the venv first, or use
`python -m substation_retrieval <command>`).

## 3. Run (every time)

```powershell
.\.venv\Scripts\activate
sr serve              # http://127.0.0.1:8077 ; starts Neo4j by itself if it is not running
```

`sr neo4j-status` shows whether Neo4j is up (with the tail of `var\neo4j\server.log`);
`sr neo4j-stop` stops it. Neo4j Browser (optional) is at http://127.0.0.1:7474 (user `neo4j`, password from `graph.neo4j_password`). `.\run.ps1` does install + start + first-run import + serve in one go.

## 4. Extract drawings with the LLM (extraction v2)

```powershell
sr health                                                  # "llm": {"available": true, ...}
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
sr ingest "D:\new_drawings\Site A.pdf" --no-v1 --wait   # --no-v1 forces the LLM even if a v1 result exists
sr ingest "D:\new_drawings" --no-v1 --wait              # a whole folder
```

You can also use the UI: **Library & ingest → choose files → untick "reuse v1 extraction" → Upload & ingest**.
Each file is a job: extract → review gate → Neo4j upsert → RAG upsert → new index version.

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

The setting is `engines.rag.image_embedder: auto`: the embedder switches on only after `sr setup-embedder` has saved
the model into `var\models`. Until then nothing imports torch or transformers, so a missing or broken ML install
cannot stop the app.

## 7. Configuration

`config\settings.yaml` holds all knobs. Environment variables override them:

| Setting | Env var | Default |
|---|---|---|
| graph backend | `SR_GRAPH_BACKEND` | `neo4j` (`sqlite` = tests/offline only) |
| Neo4j URI / user / password | `SR_NEO4J_URI`, `SR_NEO4J_USER`, `SR_NEO4J_PASSWORD` | `bolt://localhost:7687`, `neo4j`, `substation-local` (local server only) |
| retrieval mode | `SR_RETRIEVAL_MODE` | `auto` (`kg`, `rag`, `both`) |
| engines on/off | `CSS_KG_ENABLED`, `CSS_RAG_ENABLED` | `true` |
| LLM model / style | `SR_LLM_MODEL`, `SR_LLM_API_STYLE` | `gpt-6-astra` (model id), `responses` (`chat` for gateways) |
| drawings / v1 / workspace | `SR_DATA_DIR`, `SR_V1_DIR`, `SR_VAR_DIR` | `..\..\data\all_data\css\pdf`, `...\css_parsed`, `.\var` |

The scoring weights, verdict thresholds (0.80 / 0.60), plot-clearance norms and catalogue tolerances are also in
`settings.yaml`.

## 8. Workspace (`var\`, gitignored)

| Path | Content |
|---|---|
| `var\neo4j\` | Neo4j install + its data (`data`), logs (`logs`, `server.log`) |
| `var\db\index.sqlite` | document registry, RAG index (features, mask rasters, text, image embeddings), index versions |
| `var\db\jobs.sqlite` | ingest job queue |
| `var\extractions\<doc_id>\doc.json` | schema-2.1 extraction per drawing (source of truth for `sr rebuild`) |
| `var\artifacts\<doc_id>\` | page renders, crops, overlays, masks, raw LLM JSON |
| `var\cache\llm\`, `var\models\`, `var\reports\` | LLM cache, DINOv2 weights, extraction reports |

## 9. Commands

```
sr serve | import-v1 | ingest PATH... [--wait] [--no-v1] [--force] | extract FILE | extract-all [--workers N] [--index]
sr rebuild [--rag-only] | search "TEXT" [--mode hybrid|kg|rag|auto|both] | jobs | health | versions | setup-embedder | worker
sr neo4j-start | neo4j-status | neo4j-stop | neo4j-check
python -m pytest        # 18 tests, synthetic data, no network
```

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `Neo4j at bolt://localhost:7687 is not reachable` | `sr neo4j-start`; if it fails, `sr neo4j-status` shows the Java version and the server log tail |
| `Java not found` and the portable download fails | `winget install Microsoft.OpenJDK.17`, open a new terminal (or set `JAVA_HOME`) |
| `"llm": {"available": false}` | the key line is missing in `.env`; check the name `OPENAI_API_KEY` (or `ASTRA_API_KEY`) |
| LLM 404 / connection error | set `OPENAI_BASE_URL` for your gateway, or `SR_LLM_API_STYLE=chat` |
| Search says "fallback to RAG" | expected when the query is thin, or Neo4j is down; see the trace line |
| Masks / drawing views say "no image" | for v1 drawings, `SR_V1_DIR` must point at `css_parsed`; for v2, keep `var\artifacts` |

## Layout

```
src/substation_retrieval/
  core/        schema 2.1, ontology, equipment catalogue + rules, utils
  geometry/    polygons, ALS (alignment + set matching), compatibility checks, mask rasters, DINOv2 embedder
  extraction/  PDF/DXF/DWG adapters, LLM client + prompts, segmentation, scale, pipeline, batch, v1 importer
  kg/          partition builder, Neo4j (Cypher) + SQLite stores, KG matcher
  rag/         index (SQLite) + RAG engine
  retrieval/   query model + parser, site-plan encoder, shared scoring, orchestrator, overlays
  ingest/      job queue + workers, ingestion service
  chat/        chat agent
  api/         FastAPI app, tree views, drawing/mask images, static UI
(Neo4j itself is managed by `sr neo4j-*`; the constraint and index are created by the app on connect)
```
