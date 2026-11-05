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
- **Workbench UI** at http://127.0.0.1:8077, styled with the Nablon Meridian design system (SLIDe), with two pages:
  - **Create new** (`/new`): a step-by-step query builder or a plain-text prompt, plus an optional site plan
  - **Review drawings** (`/review`): drawings sorted by open actions, each shown with its fields over the
    original sheet; correct a value, mark it right or wrong, submit to re-index
- **Classic UI** at http://127.0.0.1:8077/classic with five tabs:
  - Chat
  - Library & ingest
  - Ontology (tree)
  - Site graph (design tree / network + site summary + masks over the original drawing)
  - Admin



---

## 1. Prerequisites (Ubuntu 22.04 / 24.04)

```bash
sudo apt update
sudo apt install -y git curl ca-certificates libglib2.0-0     # libglib2.0-0 is needed by opencv-python-headless
curl -LsSf https://astral.sh/uv/install.sh | sh                # installs uv into ~/.local/bin
source ~/.bashrc                                               # or open a new shell, so `uv` is on PATH
uv --version
uv python install 3.12                                         # uv-managed Python; the system Python is not touched
```

| Need | Notes |
|---|---|
| uv | package + environment manager (installed above) |
| Python 3.12 | managed by uv. Any Python ≥ 3.10 works |
| Java 17 or 21 (for Neo4j) | nothing to do: if Java is missing or older than 17, `sr neo4j-start` downloads a portable Java 17 runtime into `var/jre` (~46 MB, no sudo). To use a system JDK instead: `sudo apt install -y openjdk-17-jre-headless` |
| LLM key | in `.env` (see 2.3) |
| Drawings | PDFs in `../../data/all_data/css/pdf` (v1 parsed results in `.../pdf/css_parsed`), relative to the repo folder. Override with `SR_DATA_DIR` / `SR_V1_DIR` |
| Network | outbound HTTPS to `dist.neo4j.org` and `api.adoptium.net` (or `github.com`) on the first Neo4j start, plus PyPI and your LLM endpoint |

No docker and no sudo are needed for the app itself. All commands below run from the repo folder.

## 2. First-time setup

### 2.1 Get the code

```bash
git clone <repo-url> substation_retrieval
cd substation_retrieval
git checkout neo4_demo_code
```

### 2.2 Create the uv environment

**Option A: `uv sync` (recommended).** Dependencies come from `pyproject.toml`; versions are pinned in `uv.lock`.

```bash
uv python pin 3.12          # writes .python-version (once; commit it)
uv sync --extra all         # creates .venv, installs runtime deps + ezdxf + anthropic + pytest,
                            # installs this package in editable mode (gives the `sr` command), writes uv.lock
```

Extras you can pick instead of `all`:

| Extra | Adds | When |
|---|---|---|
| `cad` | `ezdxf` | DXF/DWG drawings (DWG also needs ODA File Converter or LibreDWG on PATH) |
| `anthropic` | `anthropic` | `llm.provider: anthropic` |
| `dev` | `pytest` | running tests |
| `ml` | `transformers` | DINOv2 image embeddings (see section 6; torch is installed separately) |

Example: `uv sync --extra cad --extra dev`. Run `uv sync` again after every `git pull`; it only installs what changed.
Commit `uv.lock` so everyone gets the same versions.

**Option B: requirements files.** Use this if you do not want a lock file.

```bash
uv venv --python 3.12
uv pip install -r requirements.txt              # runtime deps
uv pip install -r requirements-optional.txt     # ezdxf, anthropic, pytest
uv pip install -e .                             # installs the `sr` command into .venv
```

`requirements.txt` mirrors `pyproject.toml`. If you add a dependency, put it in both
(`uv add <pkg>` updates `pyproject.toml` and `uv.lock`; then add the same line to `requirements.txt`).

### 2.3 Configure secrets (`.env`)

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Set `OPENAI_API_KEY=...` (`ASTRA_API_KEY` also accepted). Add `OPENAI_BASE_URL=...` if the LLM is behind a
gateway. The app reads `.env` from the repo folder or any parent folder. `.env` is gitignored; never commit it.

No secrets are stored in the repo:

| Secret | Where it comes from |
|---|---|
| LLM key | `.env` only. **Optional**: without a key, extraction replays the committed LLM cache (section 4.1) |
| Neo4j password | `SR_NEO4J_PASSWORD` in `.env` if set; otherwise `sr neo4j-start` generates a random one into `var/neo4j/.password` (mode 600, gitignored) and the app reads it from there |

### 2.4 Start Neo4j and load the reviewed drawings

```bash
uv run sr neo4j-start      # checks Java, downloads Neo4j Community once (~130 MB) into var/neo4j, sets the password, starts it
uv run sr neo4j-check      # graph reachable, constraint and index created
uv run sr import-v1        # loads the 50 reviewed drawings into Neo4j + the RAG index (no LLM cost, ~20 s)
uv run sr health           # "llm": {"available": true, ...} confirms the key was picked up (false is fine in cache-only mode)
```

`sr` is this repo's command-line tool. Three equivalent ways to call it:

- `uv run sr <command>` (no activation needed; also syncs the env first)
- `source .venv/bin/activate` once per shell, then `sr <command>`
- `python -m substation_retrieval <command>` inside the activated venv

The rest of this README uses plain `sr` and assumes the venv is active.

## 3. Run (every time)

```bash
uv run sr serve            # UI at http://127.0.0.1:8077 ; starts Neo4j by itself if it is not running
```

Or activate first: `source .venv/bin/activate`, then `sr serve`.

One-shot alternative: `./run.sh` does install + Neo4j start + first-run import + serve (`chmod +x run.sh` once).
Extra arguments go to `sr serve`.

**Remote Ubuntu server / VM.** The UI binds to `127.0.0.1` by default. Either tunnel from your laptop:

```bash
ssh -L 8077:127.0.0.1:8077 <user>@<server>     # then open http://127.0.0.1:8077 locally
```

or bind to all interfaces with `sr serve --host 0.0.0.0` (port `--port 8077`) and open the port only to trusted IPs
(`sudo ufw allow from <your-ip> to any port 8077`). The UI has no login, so do not expose it publicly.

**Keep it running after logout:** run it inside `tmux` (`tmux new -s sr`, then `uv run sr serve`, detach with
`Ctrl-b d`), or as a systemd user service:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/substation-retrieval.service <<UNIT
[Unit]
Description=substation_retrieval UI
After=network-online.target

[Service]
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/.venv/bin/sr serve
Restart=on-failure

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now substation-retrieval
loginctl enable-linger $USER          # keep user services running when you are logged out
journalctl --user -u substation-retrieval -f
```

Other run-time commands:

| Command | What it does |
|---|---|
| `sr neo4j-status` | whether Neo4j is up, with the tail of `var/neo4j/server.log` |
| `sr neo4j-stop` | stops Neo4j |
| `uv run pytest` | test suite (synthetic data, no network) |

Neo4j Browser (optional) is at http://127.0.0.1:7474 (user `neo4j`, password: `cat var/neo4j/.password`, or your `SR_NEO4J_PASSWORD`).

## 4. Extract drawings with the LLM (extraction v2)

```bash
sr health                                                  # "llm": {"available": true, ...}
sr extract "../../data/all_data/css/pdf/CSS layout 10.02.25.pdf" --out var/test_v2.json   # one drawing, not indexed
sr extract-all --workers 4                                 # all PDFs in paths.data_dir, report vs v1
sr extract-all --workers 4 --index                         # same, and replace the v1 entries in the graph + index
```

What `extract-all` writes:

- `var/extractions/<doc_id>/doc.json` for each drawing
- debug artifacts in `var/artifacts/<doc_id>/`: page renders, layout crops, overlays, `masks/*.png`, raw LLM JSON
- a comparison CSV in `var/reports/extract_v2_<time>.csv`, covering substations, equipment, transformers,
  room L/W, clear height, scale source, section and kVA (v2 vs v1)

LLM answers are cached by file hash + prompt version, so re-runs only pay for what changed.

Adding new drawings later, one at a time or in bulk:

```bash
sr ingest ~/new_drawings/"Site A.pdf" --no-v1 --wait   # --no-v1 forces the LLM even if a v1 result exists
sr ingest ~/new_drawings --no-v1 --wait              # a whole folder
```

You can also use the UI: **Library & ingest → choose files → untick "reuse v1 extraction" → Upload & ingest**.
Each file is a job: extract → review gate → Neo4j upsert → RAG upsert → new index version.

### 4.1 LLM cache (committed, works without a key)

Every LLM reply is saved in `var/cache/llm/<sha256>__<prompt_version>__<pass>.json`. This folder **is committed to git**
(the rest of `var/` is not), so a fresh clone replays all past extractions with no key and no cost:

```bash
sr extract-all --workers 4 --index     # no key: cached drawings replay from var/cache/llm, others are "skipped" in the report
```

How it behaves:

| Situation | Result |
|---|---|
| key set, reply cached | cache used, no API call |
| key set, not cached | API call, reply added to the cache |
| no key, drawing cached | cache replayed, full v2 extraction |
| no key, not cached | `extract-all` skips it; `sr ingest` / UI upload fall back to text-only extraction |

The cache is keyed on the drawing's sha256 and `extraction.prompt_version` (`config/settings.yaml`). Bumping the prompt
version invalidates it. After a run with a key, commit the new replies:

```bash
git add var/cache/llm && git commit -m "LLM cache: new replies"
```

The replies contain extracted drawing content (client data), so keep the repo private.

## 5. Using the UI

### 5.1 Workbench (`/`)

The workbench is a React app in `frontend/`, styled with the Nablon Meridian design system:
`frontend/src/theme/meridian.css` is copied unchanged from `adani-slide-main/Nablon-Meridian-UX-Styling`,
and `workbench.css` adds this app's components on Meridian tokens. Follow Meridian's rules when changing
it: brand blue is the only accent, pass/live is blue (no green), amber = needs attention, red = fail,
flat white cards. Fonts (Poppins, Inter, JetBrains Mono) load from Google Fonts. `npm run build` writes it into
`src/substation_retrieval/api/web/`, which `sr serve` serves at `/`. The build is committed, so running the
app needs no Node. If `api/web/` is missing, `/` falls back to the classic UI.

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to a running `sr serve` on :8077
npm run build      # refresh api/web after UI changes
```

- **Create new.** *Step by step* asks, in plain words, where the substation sits, the equipment list
  (count × type, kVA, dry/oil, ways), room size and height in metres, doors and civil features, and nearby
  nalla / DP road / tree distances. It is sent as a structured query (`POST /api/search`), so only the
  fields filled in are scored. *Describe in words* is the chat (`POST /api/chat`) with follow-ups. Each
  result shows the verdict, score, failing checks, the original equipment layout and the historic room
  posed on the new site.
- **Review drawings.** `GET /api/workbench/queue` lists every drawing with its open actions, most first:
  extraction not yet reviewed (or held at the gate), the extractor's quality issues, room length / width /
  clear height not read, plot clearances under the norm, and items a reviewer marked wrong or unsure.
  Opening a drawing (`GET /api/workbench/documents/{id}`) shows the page with every located field drawn
  on it; click a field or a box to jump between them. Numbers and labels are editable (geometry is not),
  and a missing value can be added.
  - *Save draft* (`PATCH .../fields`) writes the edits into `var/extractions/<id>/doc.json` and logs each
    one under `review.edits`. Nothing reaches the index yet.
  - *Submit review* (`POST .../submit`) saves, marks the drawing reviewed and re-indexes it (graph + RAG,
    new index version). Needs Neo4j up; if indexing fails the edits are kept and you can submit again.
  - Correct / wrong / unsure marks and notes live in `var/review/marks/<id>.json`.

### 5.2 Design inputs, repository and rules

- **Create new → Design inputs** (the detailed design's screen C7). Pick CSS / DSS and the target stage,
  the site type (open to sky on an open plot or RG, or part of a building with a basement or mother earth
  below), the floor and clear height, and paste the equipment the way Network Planning sends it
  (`1. 1.5 MVA DT – 3 nos; 2. 2+2 RMU – 1 nos`). Door mode, door positions and constraints travel with the
  request. Mark each document of the AEML mandatory input package (T1–T9, plus D1–D3 for detailed) Yes /
  No / NA; anything mandatory not marked Yes is carried onto the results as an open item.
  *Read the request* (`POST /api/workbench/understand`) shows what was understood before matching.
- **Browse repository** (`/repo`) follows the design's hierarchy: Category → Division → Project (ZLDP, or
  developer where no number is allocated) → Stage folder → Revision. It reads the VidyutMitra
  SubstationRepository (`_system/metadata.json` and `drawings/`) **read only**; uploads, versions and
  approval stay with VidyutMitra. Point `repository.path` in `config/settings.yaml` (or `SR_REPO_PATH`) at
  the SubstationRepository folder. Repository files that are also in this library get a *Fields* link to
  the review page (matched by content hash, else by name), and a project's parsed parameters can be sent
  to Create new with *Use as design inputs*. The page also has *Find a drawing* (B6) over the same
  parameters.
- **Design rules** (`/rules`, R5): browse by section, search, filter (to confirm, edited, drives a check),
  add, edit, duplicate, delete, reset to extracted, export / import JSON, and *Load workbook* to rebuild
  the rules from `Equip_DimensionRules.xlsx` with `scripts/build_rules.py` (needs `openpyxl`:
  `uv pip install openpyxl`). Rules added by hand survive a reload unless you untick the option. Changes
  need the admin role; rules tied to a check change it on save.

### 5.3 Sign-in and roles

Every page and every `/api` call except `/api/health` needs a sign-in (`api/auth.py`). The session is a
signed HttpOnly cookie that lasts `auth.session_hours` (12 by default). `/classic` sends you to the same
sign-in page.

| Role | Can |
| ---- | --- |
| Viewer | browse, search, chat, open drawings in review (read only) |
| Editor | + edit, mark and submit drawings, ingest drawings |
| Admin  | + engines, import / rebuild, delete drawings, manage users, edit / load design rules |

On first start `var/auth/users.json` is created with the VidyutMitra development accounts:
`admin` / `Admin@123`, `draftsman` / `Editor@123`, `viewer` / `Viewer@123`. The sign-in page lists them
until all three passwords are changed. Change them before anyone else uses the app:

```bash
sr users                                   # list accounts
sr user-passwd admin                       # set a password (prompts)
sr user-add asha --role editor --name "Asha K"   # new account (prompts for the password)
sr user-remove viewer
```

Passwords are stored as scrypt hashes in the same format as VidyutMitra's `_system/users.json`, so that
file can be copied to `var/auth/users.json`. The signing key is `var/auth/secret` (random) or
`SR_AUTH_SECRET`. Set `auth.secure_cookie: true` when serving over HTTPS. For a single-user local run,
`SR_AUTH_ENABLED=false` turns sign-in off and everyone is admin.

### 5.4 Classic UI (`/classic`)

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

```bash
uv pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU-only torch (~200 MB)
uv pip install -r requirements-ml.txt                                   # transformers
sr setup-embedder        # saves facebook/dinov2-small (ViT-S/14, 22M params) to var/models
sr rebuild               # embeds every substation's mask raster (no LLM)
```

The setting is `engines.rag.image_embedder: auto`: the embedder switches on only after `sr setup-embedder` has saved
the model into `var/models`. Until then nothing imports torch or transformers, so a missing or broken ML install
cannot stop the app.

## 7. Configuration

`config/settings.yaml` holds all knobs. Environment variables override them:

| Setting | Env var | Default |
|---|---|---|
| graph backend | `SR_GRAPH_BACKEND` | `neo4j` (`sqlite` = tests/offline only) |
| Neo4j URI / user / password | `SR_NEO4J_URI`, `SR_NEO4J_USER`, `SR_NEO4J_PASSWORD` | `bolt://localhost:7687`, `neo4j`, generated into `var/neo4j/.password` |
| retrieval mode | `SR_RETRIEVAL_MODE` | `auto` (`kg`, `rag`, `both`) |
| engines on/off | `CSS_KG_ENABLED`, `CSS_RAG_ENABLED` | `true` |
| LLM model / style | `SR_LLM_MODEL`, `SR_LLM_API_STYLE` | `gpt-6-astra` (model id), `responses` (`chat` for gateways) |
| drawings / v1 / workspace | `SR_DATA_DIR`, `SR_V1_DIR`, `SR_VAR_DIR` | `../../data/all_data/css/pdf`, `.../css_parsed`, `./var` |

The scoring weights, verdict thresholds (0.80 / 0.60), plot-clearance norms and catalogue tolerances are also in
`settings.yaml`.

## 8. Workspace (`var/`, gitignored except `var/cache/llm/`)

| Path | Content |
|---|---|
| `var/neo4j/` | Neo4j install + its data (`data`), logs (`logs`, `server.log`) |
| `var/db/index.sqlite` | document registry, RAG index (features, mask rasters, text, image embeddings), index versions |
| `var/db/jobs.sqlite` | ingest job queue |
| `var/extractions/<doc_id>/doc.json` | schema-2.1 extraction per drawing (source of truth for `sr rebuild`) |
| `var/artifacts/<doc_id>/` | page renders, crops, overlays, masks, raw LLM JSON |
| `var/cache/llm/` | LLM replies, **committed** (section 4.1) |
| `var/neo4j/.password` | generated Neo4j password (when `SR_NEO4J_PASSWORD` is not set) |
| `var/models/`, `var/reports/` | DINOv2 weights, extraction reports |

## 9. Commands

```
sr serve | import-v1 | ingest PATH... [--wait] [--no-v1] [--force] | extract FILE | extract-all [--workers N] [--index]
sr rebuild [--rag-only] | search "TEXT" [--mode hybrid|kg|rag|auto|both] | jobs | health | versions | setup-embedder | worker
sr neo4j-start | neo4j-status | neo4j-stop | neo4j-check
uv run pytest           # 18 tests, synthetic data, no network
```

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `Neo4j at bolt://localhost:7687 is not reachable` | `sr neo4j-start`; if it fails, `sr neo4j-status` shows the Java version and the server log tail |
| `Java not found` and the portable download fails | `sudo apt install -y openjdk-17-jre-headless`, open a new shell (or set `JAVA_HOME`) |
| `"llm": {"available": false}` | expected in cache-only mode; to call the LLM, add `OPENAI_API_KEY` (or `ASTRA_API_KEY`) to `.env` |
| Neo4j auth error after upgrading an old install | the old default password was `substation-local`: put `SR_NEO4J_PASSWORD=substation-local` in `.env`, or delete `var/neo4j/data/dbms/auth.ini` and run `sr neo4j-start` again |
| LLM 404 / connection error | set `OPENAI_BASE_URL` for your gateway, or `SR_LLM_API_STYLE=chat` |
| Search says "fallback to RAG" | expected when the query is thin, or Neo4j is down; see the trace line |
| Masks / drawing views say "no image" | for v1 drawings, `SR_V1_DIR` must point at `css_parsed`; for v2, keep `var/artifacts` |

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
