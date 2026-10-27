# substation_retrieval

Design reuse for AEML consumer substations (CSS). A new site's requirement goes in, as text, structured JSON
or a builder's site plan. The closest historic CSS designs come back, each with a reuse / adapt / new verdict,
the checks that pass or fail, and an overlay of the historic layout placed on the new site.

- **Extraction v2** turns a drawing (PDF now, DXF/DWG first cut) into a schema-2.1 JSON:
  - layouts, scale, section heights, equipment, openings, beams, cutouts, trenches
  - site features and mask polygons
  - plot-clearance self-check
  - catalogue-inferred transformer rating
- **Knowledge graph (primary engine).** Every drawing becomes a graph partition typed by the CSS ontology
  (5 families, 40+ classes). Matching works in four layers:
  - Hungarian vertex pairing (Wu-Palmer × attributes × geometry)
  - IDF-weighted edge consistency
  - Aligned Layout Score
  - facet rarity
- **RAG (fallback engine).** The shortlist comes from BM25 text + a feature vector + canonical mask rasters,
  then is re-ranked with the same geometry and compatibility library.
- **Orchestrator.** `retrieval_mode` is `kg | rag | auto | both`, and each engine has its own enable flag.
  `auto` falls back to RAG when the graph is down or slow, the query graph is too small, or KG candidates are weak.
- **Incremental ingestion.** Each pushed file (or bulk folder) becomes a job:
  extract → review gate → graph upsert (delete by partition) → RAG upsert → new index version.
  Retrieval reloads on the next version. No full rebuild is needed.
- **UI** at `/` with five tabs:
  - chat with site-plan upload and result overlays
  - library & ingest jobs
  - ontology tree / graph
  - per-site knowledge graph with extracted geometry and debug masks
  - admin engine toggles

Busbar, fault level, floor loading, interconnections and SLDs are out of scope by decision.

## Install (uv, no docker)

```bash
cd substation_retrieval
uv venv                              # .venv (Python >= 3.10)
uv pip install -r requirements.txt   # runtime deps
uv pip install -e .                  # the package + `sr` command
uv pip install -r requirements-optional.txt   # optional: gremlinpython, ezdxf, anthropic, pytest
```

### Keys

Put the keys in a `.env` file, in this folder or any parent folder
(e.g. `C:\Users\ibpha\work\projects\.env`):

```
ASTRA_API_KEY=...            # GPT-6 Astra via the OpenAI-compatible API (OPENAI_API_KEY also accepted)
# OPENAI_BASE_URL=...        # if Astra sits behind a gateway
```

Without a key, these still work:

- the v1 import
- KG/RAG search
- chat (rule parser + templated answers)

New PDFs fall back to text-only extraction and are marked provisional. Keys are never logged or returned by the API.

## Run

```bash
sr import-v1                 # index the 50 reviewed v1 extractions (css_parsed), no LLM cost
sr serve                     # http://127.0.0.1:8077  (API + UI + in-process ingest worker)
sr search "indoor CSS ground floor, 2 x 1500 kVA DT, 1 RMU, 4 LTP, room 11 x 8 m, clear height 4.5 m" --mode auto
sr ingest path/to/new.pdf --wait          # or a folder; --no-v1 forces the v2 LLM pipeline
sr extract path/to/new.pdf --out new.json # extraction only, no indexing
sr rebuild                   # re-index all stored extractions (no LLM)
python -m pytest             # tests (synthetic fixtures, no client data, no network)
```

Paths come from `config/settings.yaml` and can be overridden with environment variables:

- `SR_DATA_DIR`: the PDFs, default `../../data/all_data/css/pdf`
- `SR_V1_DIR`: `css_parsed`
- `SR_VAR_DIR`: the workspace

## Workspace (`var/`, gitignored)

| path | content |
|---|---|
| `var/db/kg.sqlite` | knowledge graph (vertices / edges, partition per drawing) |
| `var/db/index.sqlite` | document registry, RAG index (features, mask rasters, text), index versions |
| `var/db/jobs.sqlite` | ingest job queue |
| `var/extractions/<doc_id>/doc.json` | schema-2.1 extraction per drawing (source of truth for rebuilds) |
| `var/artifacts/<doc_id>/` | page renders, layout crops, overlays, `masks/*.png`, raw LLM JSON |
| `var/cache/llm/` | LLM responses keyed by file sha + prompt version |
| `var/uploads/`, `var/siteplans/`, `var/inbox/` | uploaded drawings, analysed site plans, watched folder |

## API (selection)

| method | path | purpose |
|---|---|---|
| POST | `/api/chat` | conversational search; follow-ups answered from the last results |
| POST | `/api/search` | `{text, structured, siteplan_id, mode, k}` |
| POST | `/api/siteplan` | upload a builder plan (red = proposed CSS area; blue nalla, yellow wayleave, green trench) |
| POST | `/api/ingest` | multipart files → jobs. `/api/ingest/paths` takes server-side files / folders (AEML integration hook) |
| GET | `/api/jobs`, `/api/documents`, `/api/versions` | status |
| GET | `/api/documents/{id}/graph`, `/geometry` | per-site knowledge graph and extracted geometry |
| GET | `/api/ontology` | class tree with instance counts (always available) |
| GET/PUT | `/api/engines` | `retrieval_mode`, `kg_enabled`, `rag_enabled` |
| GET | `/api/health` | engines, graph, index, jobs, LLM |

## Layout

```
src/substation_retrieval/
  core/        schema 2.1, ontology, equipment catalogue + rules, utils
  geometry/    polygons, ALS (alignment + set matching), compatibility checks, mask rasters
  extraction/  PDF/DXF/DWG adapters, LLM client + prompts, segmentation, scale, pipeline, v1 importer
  kg/          partition builder, SQLite + Gremlin stores, KG matcher
  rag/         index (SQLite) + RAG engine
  retrieval/   query model + parser, site-plan encoder, shared scoring, orchestrator, overlays
  ingest/      job queue + workers, ingestion service
  chat/        chat agent
  api/         FastAPI app + static UI
scripts/janusgraph/   JanusGraph configs (BerkeleyDB-JE/Lucene local, Bigtable on GCP) + schema
```

## Scoring summary

- **Presence rule.** A score component is used only when the query supplies it. Coverage reports how much of
  the scoring the query could use.
- **ALS weights.** footprint 3, equipment positions 3, columns/beams 3, cutouts 2, doors 2, height 2, trench 1,
  louvres 0.5, site 1.
- **Pose search.** 8 poses (4 rotations × mirror), then ±10° and ICP refinement.
- **S_KG.** `0.30 nodes + 0.25 edges + 0.30 geometry + 0.15 facets`, times `c_count` when a substation count is
  given, times the hard-facet penalty when installation, arrangement or voltage contradicts the query.
- **Verdict.** `reuse` at ≥ 0.80 with no failed check; `adapt` at ≥ 0.60; otherwise `new`.
- **Compatibility checks:**
  - footprint fit and equipment inside the new outline
  - catalogue slot fit for requested kVA / ways
  - clear height vs tallest equipment + 450 mm tray rule and the 4.2 m CSS minimum
  - beams, columns and slab cutouts of the new site
  - door width for transformer moves (2750 mm)
  - trench
  - plot clearances for the historic layout placed at the new site, and the historic design's own violations:
    nalla 6 m, DP road 4.5 m, internal road 1.5 m, compound wall 1.5 m, structure 6 m, tree 1.5 m (4.5 m at doors)
