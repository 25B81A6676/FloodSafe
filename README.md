# FloodSafe

**Flash Flood Prediction System for Hilly Regions using Multi-Source Data**
Smart India Hackathon — Problem Statement **SIH26192** · Theme: Disaster Management

FloodSafe fuses five independent, free, keyless open data sources into a transparent
flash-flood susceptibility score for a configurable hilly pilot region, visualises it
on a GIS risk map, explains every score, and lets an operator simulate a cloudburst
scenario end to end.

> **This is a decision-support prototype, not a certified operational warning system.**
> It does not issue evacuation orders and must never be the sole basis for an
> emergency decision. Its limitations are stated in [Limitations](#limitations) and
> reproduced in the application's own Methodology page.

---

## The problem

Flash floods in the Himalaya are the hardest class of flood to predict:

- **Response times are minutes, not hours.** A steep catchment concentrates runoff so
  fast that a settlement can be hit before a rain gauge reading is even processed.
- **The trigger is small and local.** Cloudbursts — which the India Meteorological
  Department defines as more than 100 mm of rain in one hour — occur at a scale below
  most operational forecast grids.
- **Gauge coverage is sparse.** The valleys most at risk have the fewest instruments.
- **Rainfall alone is a poor predictor.** 60 mm on dry ground in May is harmless; the
  same 60 mm on a saturated catchment in August is dangerous. Antecedent wetness,
  slope, and channel proximity decide the outcome.

No single data source answers the question. That is precisely why this problem
statement asks for *multi-source* data.

## The solution

FloodSafe combines four physically distinct signals so they can corroborate each
other, then scores them with a transparent, fully configurable model.

```
                          MULTI-SOURCE DATA
                                  |
        +------------------+------+------+-------------------+
        |                  |             |                   |
     WEATHER            TERRAIN      HYDROLOGY          CLIMATOLOGY
  Open-Meteo         Copernicus     GloFAS + OSM         ERA5 archive
  (live rainfall)    DEM GLO-90     (discharge,          (5-yr local
                     -> slope,       river distance,      rainfall
                        relief       drainage density)    distribution)
        |                  |             |                   |
        +------------------+------+------+-------------------+
                                  v
                          DATA VALIDATION
                    (bounds, staleness, duplicates)
                                  v
                        DATA NORMALISATION
                 (piecewise-linear curves -> 0..1)
                                  v
                       FEATURE ENGINEERING
                       (12 features, provenance-tagged)
                                  v
                        FLOOD RISK ENGINE
                   (configurable weighted model)
                                  v
                         RISK SCORE 0-100
                                  |
        +--------+--------+-------+--------+---------+
        v        v        v                v         v
      SAFE      LOW    MODERATE           HIGH    EXTREME
        +--------+--------+-------+--------+---------+
                                  v
                           GIS RISK MAP
                                  v
                        DISASTER DASHBOARD
                                  v
                              ALERTS
```

### What makes it more than a dashboard

- **Real hydrology, not just weather.** Copernicus **GloFAS** modelled river discharge
  is an independent signal that does not derive from the rainfall feed, so agreement
  between them is genuine corroboration.
- **Terrain is computed, not looked up.** Slope, aspect and local relief are derived
  here by finite-difference gradient over a 3×3 DEM stencil sampled at ±500 m.
- **River proximity is real geometry.** True point-to-polyline distance against
  OpenStreetMap waterway geometry — not a bounding-box guess.
- **Rainfall is put in local context.** ERA5 reanalysis gives each location its own
  5-year rainfall distribution for the current calendar window, so today's total is
  reported as a **percentile of that place's own history**.
- **Soil wetness is modelled.** The Antecedent Precipitation Index
  (`API = Σ kⁱ·P₍t−i₎`, k = 0.9 over 14 days) is a standard hydrological proxy for how
  much of the next millimetre becomes runoff.
- **The risk map is not one big red circle.** A 6×7 grid of cells is each scored from
  *its own* real weather, DEM elevation, GloFAS discharge and river distance. In
  testing, 42 cells produced 42 distinct elevations and 42 distinct river distances.

## Data sources

| Source | Contributes | Key? | Licence |
|---|---|---|---|
| [Open-Meteo Forecast API](https://open-meteo.com/) | Current + hourly precipitation, temperature, humidity, wind, pressure, 48 h forecast | No | CC BY 4.0 |
| [Copernicus GloFAS](https://global-flood.emergency.copernicus.eu/) via Open-Meteo Flood API | Modelled daily river discharge, 30 d history + 7 d forecast | No | Free & open |
| [Copernicus DEM GLO-90](https://open-meteo.com/en/docs/elevation-api) via Open-Meteo Elevation | Ground elevation → derived slope, aspect, relief | No | Free & open |
| [ECMWF ERA5](https://open-meteo.com/en/docs/historical-weather-api) via Open-Meteo Archive | Multi-year daily rainfall climatology | No | Free & open |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) via Overpass API | River/stream geometry, hospitals, schools, bridges, settlements | No | ODbL |
| [OpenTopoData](https://www.opentopodata.org/) · [Open-Elevation](https://open-elevation.com/) | Elevation fallbacks | No | Free |

**No paid service is required, and no API key is used anywhere in this project.**

## Risk methodology

The active model is `BaselineRiskModel` — a transparent weighted sum. Every weight,
normalisation breakpoint and class boundary lives in
[`data/config/risk_weights.json`](data/config/risk_weights.json) and can be retuned
without touching code.

| Feature | Group | Weight | Grounding |
|---|---|---:|---|
| Rainfall intensity | meteorological | 0.16 | 100 mm/h endpoint = IMD cloudburst threshold |
| Soil saturation (API) | hydrological | 0.10 | Standard antecedent precipitation index |
| River discharge anomaly | hydrological | 0.10 | GloFAS today ÷ its own 30-day mean |
| 3-hour accumulation | meteorological | 0.09 | Catchment response window |
| 24-hour accumulation | meteorological | 0.09 | IMD daily rainfall classes |
| Forecast rainfall (24 h) | meteorological | 0.09 | Provides lead time |
| Terrain slope | terrain | 0.09 | Finite-difference gradient on DEM |
| Proximity to river network | hydrological | 0.08 | Point-to-polyline distance (OSM) |
| Rainfall anomaly vs climatology | meteorological | 0.07 | ERA5 percentile for this location |
| Rainfall trend | meteorological | 0.05 | Intensifying vs decaying storm |
| Local terrain relief | terrain | 0.05 | Runoff concentration |
| Stream network density | hydrological | 0.03 | Drainage density from OSM |

Each raw value is mapped to 0–1 by an explicit piecewise-linear curve, multiplied by
its weight, and summed:

```
risk_score = 100 × Σ(wᵢ · normalise(featureᵢ)) / Σ(wᵢ over available features)
```

| Score | Class |
|---|---|
| 0–20 | SAFE |
| 21–40 | LOW |
| 41–60 | MODERATE |
| 61–80 | HIGH |
| 81–100 | EXTREME |

### Missing data is renormalised, never zeroed

If a source fails, its features are dropped and the remaining weights are
**renormalised**. Substituting zero would quietly *lower* the risk score during an
outage — exactly the wrong behaviour for a warning system. Model coverage and a
data-quality **confidence** rating (which describes the *data*, not flood
probability) are both surfaced in the UI.

### Explainability

Every score ships with a ranked contributor list computed from the same weighted
contributions that produced the number, so the explanation cannot disagree with the
result. Each contributor carries its value, weight, share of the score, source, and
freshness.

## Technology

| Layer | Stack |
|---|---|
| Frontend | React 18, Vite, TypeScript (strict), React-Leaflet, Recharts |
| Backend | Python 3.11+, FastAPI, Pydantic v2, httpx (async) |
| Data / ML | pandas, NumPy, scikit-learn, joblib |
| Storage | SQLite (cache, observations, risk history, simulation state) |
| Mapping | OpenStreetMap tiles + Leaflet |

## Installation

Requires **Python 3.11+** and **Node 18+**.

```bash
git clone <repository-url>
cd "flashflood prediction"
```

**Backend**

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

**Frontend**

```bash
cd frontend
npm install
```

No `.env` file is needed. Copy `.env.example` to `.env` only to override defaults.

## Running

Two terminals.

**Terminal 1 — backend** (from `backend/`):

```bash
python -m uvicorn app.main:app --reload --port 8000
```

**Terminal 2 — frontend** (from `frontend/`):

```bash
npm run dev
```

Open **http://localhost:5173**. API docs are at **http://localhost:8000/docs**.

On first boot the backend warms its cache in the background (terrain, weather,
GloFAS, OSM, ERA5) so the first dashboard load is fast. Watch the logs for the
pipeline trace: `FETCH`, `CACHE-HIT`, `CACHE-MISS`, `FALLBACK`, `RISK`, `SIMULATION`.

### Presenting

For a demo, run the production bundle instead of the dev server — it starts
faster, has no hot-reload interruptions, and does not double-fetch the way React
StrictMode does in development:

```bash
npm run build && npm run preview
```

Open **http://localhost:4173**. The backend still runs on port 8000; the preview
server proxies `/api` to it.

Let the backend finish its cache warm-up (about 30 seconds, `prefetch complete`
in the log) before demonstrating, so every panel loads instantly.

## Demo script (3–5 minutes)

1. **Dashboard** opens on the pilot region with live conditions.
2. Pick a monitoring location — try **Gaurikund** or **Joshimath**.
3. Read the **Conditions** row: rainfall, DEM elevation and slope, GloFAS river
   status, soil saturation, distance to the nearest mapped watercourse.
4. Read **Why this score** — the ranked contributors with weights and shares.
5. Check **Data sources**: six feeds, each badged LIVE / CACHED / DEMO.
6. Press **Simulate flash flood**.
7. Watch the score jump to **EXTREME**, the alert escalate to **CRITICAL**, the
   Conditions card switch to **SCENARIO VALUES**, the map turn purple, and every
   contributor gain a **SIMULATED** tag.
8. Drag individual sliders — each one is a real model input, not a display scale.
9. Open **Command centre**: regional roll-up, ranked location table, and OSM-derived
   exposed infrastructure.
10. Press **Reset scenario** to return to live data.
11. Open **Methodology** for the full feature table and stated limitations.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Liveness |
| `GET /api/system/sources` | Per-source connectivity + cache statistics |
| `GET /api/system/providers` | Declared provider families and integration status |
| `GET /api/geography` | India dataset provenance and hierarchy levels |
| `GET /api/geography/states` | 28 states + 8 union territories |
| `GET /api/geography/states/{state}/districts` | Districts of one state |
| `GET /api/regions` · `/api/regions/{id}` | Region configuration and grid |
| `GET /api/locations?region_id=` | Locations for **any** scope: `india`, a state, a district, or a curated region |
| `GET /api/weather/{location_id}` | Weather + antecedent rainfall |
| `GET /api/climatology/{location_id}` | ERA5 distribution + current percentile |
| `GET /api/terrain/{location_id}` · `/api/terrain` | Elevation, slope, aspect, relief |
| `GET /api/hydrology/{location_id}` | GloFAS discharge + OSM river context |
| `GET /api/gis/layers` · `/waterways` · `/infrastructure` | Map vector layers |
| `GET /api/risk/{location_id}` | Explainable risk assessment |
| `GET /api/risk/map` | Per-cell grid risk layer |
| `GET /api/risk/model` | Full model configuration and registry |
| **`GET /api/monitoring/{location_id}`** | **Everything for one location in one call** |
| `GET /api/dashboard/summary` · `/authority` | Regional roll-ups |
| `GET /api/alerts` · `/api/alerts/{location_id}` | Advisories |
| `GET /api/simulation/scenarios` · `/controls` · `/state` | Simulator metadata |
| `POST /api/simulation/run` · `/reset` | Drive the simulator |
| `GET /api/historical/flood-events` | Labelled dataset, if the operator supplied one |

## Geographic coverage — all of India

The platform covers **28 states, 8 union territories and every district within them**,
selected through one hierarchy:

```
India → State / UT → District → Location
```

`India → Uttarakhand → Rudraprayag → Gaurikund`, `India → Kerala → Wayanad → …` and
`India → Telangana → Hyderabad → …` all work through the same code path.

**One risk engine, everywhere.** There is no per-state scoring logic. A state or
district is resolved into exactly the same region shape a curated JSON file produces,
so `risk_map_service`, `osm_service` and `flood_risk_engine` never learn that more than
one kind of region exists. A test asserts that identical feature values produce an
identical score in all twelve tested states.

**Where the geography comes from.** `data/india/states.json` and
`data/india/districts.json` are generated from OpenStreetMap administrative relations
(states are `admin_level=4`, districts `admin_level=5`) by:

```bash
python scripts/build_india_geo.py
```

The script is resumable, rotates Overpass mirrors, and **refuses to write a partial
dataset** — a mirror serving a regional extract rather than the planet is more dangerous
than one returning an error. No coordinate is hand-entered.

Each state also carries the administrative centre its own OSM relation designates. That
matters for fragmented union territories: Puducherry's four enclaves are spread across
South India, so its bounding-box centre lands in inland Andhra Pradesh, and ranking
Puducherry from that point would attribute another state's weather to it.

**Grid resolution scales with scope** (configurable in `backend/app/config/settings.py`),
because every cell costs real upstream API calls:

| Scope | Grid | Notes |
|---|---|---|
| India | 8 × 8 | Masked to land using the real state bboxes — no cells in the Bay of Bengal |
| State | 6 × 7 | |
| District | 5 × 5 | |

Locations are resolved **lazily**: a district's settlements are fetched from OpenStreetMap
the first time that district is opened, then cached and persisted. Nothing is fetched for
the ~780 districts a user never visits.

### Curated regions still win

`data/regions/*.json` takes precedence over the generated dataset, so Uttarakhand and
Himachal Pradesh keep their hand-checked rivers, confluences and monitoring locations.
Adding a richer curated region for any other state is still just a JSON file.

## Machine learning

The ML pipeline is built and wired in, but **no trained model ships and no accuracy
figure is claimed**, because there is no free, automatically obtainable, labelled
flash-flood event dataset for the pilot region. Inventing a validation score would be
worse than having none.

```bash
python ml/features.py                    # feature order + required dataset schema
python ml/train.py                       # synthetic smoke test of the pipeline
python ml/evaluate.py                    # trained vs baseline comparison
python ml/train.py --dataset <file> --activate   # deploy a genuinely trained model
```

`ml/train.py` **refuses** to write `ml/models/active_model.joblib` from synthetic
data. `SklearnRiskModel` picks that file up automatically when it exists, so a real
model can replace the heuristic with no change to the API, map or dashboard.

To supply real labels, see the schema printed by `python ml/features.py`.

## Testing

```bash
cd backend && python -m pytest          # 135 tests
cd frontend && npm run typecheck        # strict TypeScript
```

Coverage includes the risk engine (monotonicity, class boundaries, bounds,
renormalisation on missing data), explainability, all four scenarios against their
expected bands, the API contract, the geometry (distance, slope, grid tiling),
validation, and the full fallback chain.

**The entire API test suite runs with outbound networking disabled**, which proves the
platform stays usable when every upstream source is unreachable.

## Reliability

```
LIVE API  ──fails──▶  CACHE (within TTL)  ──expired──▶  STALE CACHE  ──none──▶  DEMO DATA
```

Expired cache entries are deliberately retained: a real observation from an hour ago
beats invented numbers. Each stage is labelled distinctly in the UI, so cached,
stale, simulated and demonstration values can never be mistaken for live
measurements. Overpass has a second mirror; elevation has two fallback providers.

## Limitations

- **Not calibrated against observed floods.** The score is a relative susceptibility
  index, not a probability.
- **No hydraulic simulation.** It does not model inundation depth, extent, or arrival
  time.
- **GloFAS resolves rivers on a ~5 km grid**, too coarse for small headwater
  catchments. The settlement's own cell is often a hillslope carrying almost no flow,
  so the platform selects the highest-discharge cell in a small stencil as the
  representative channel and reports the offset it used.
- **OpenStreetMap stream coverage varies**, which affects drainage density.
- **The weights are engineering judgement**, informed by IMD rainfall classes but not
  fitted to data.
- **Not an official warning source.** Verify against IMD and SDMA bulletins.

## Future work

- Supervised model (Random Forest / XGBoost) once a cited labelled dataset exists
- LSTM sequence model over rainfall and discharge history to capture catchment lag
- Hydraulic routing for inundation extent and arrival time
- IoT rain-gauge and water-level sensor ingestion
- Satellite precipitation (GPM IMERG) and change detection
- Personalised evacuation routing: risk map → user location → safest shelter →
  fastest safe route
- Mobile application and SMS/CAP alert dissemination

## How this addresses SIH26192

| Requirement | Implementation |
|---|---|
| Multi-source data | 5 independent live sources (weather, DEM, GloFAS, ERA5, OSM), each with its own provenance and freshness |
| Hilly regions | Uttarakhand pilot with 26 real Himalayan monitoring locations; slope, relief and channel proximity are first-class features |
| Flash-flood prediction | 12-feature weighted engine producing a 0–100 score, class, confidence and 48 h projection |
| GIS | Leaflet map with a 42-cell per-cell risk grid, real OSM river geometry, stations and exposed infrastructure |
| Disaster management | Severity-graded advisories with concrete actions, plus an authority command centre |
| Demonstrability | Four scenarios and nine live model-input sliders driving the real engine |
| Credibility | Nothing fabricated; every limitation stated in the product itself |

## Attribution

Weather and elevation data by [Open-Meteo](https://open-meteo.com/) (CC BY 4.0).
River discharge from the [Copernicus Emergency Management Service GloFAS](https://global-flood.emergency.copernicus.eu/).
Historical rainfall from ECMWF ERA5 reanalysis.
Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors (ODbL).

FloodSafe claims no ownership of any third-party data.

## Project structure

```
flashflood prediction/
├── backend/
│   ├── app/
│   │   ├── main.py                 FastAPI app, CORS, lifespan, error handling
│   │   ├── api/routes/             health, regions, weather, terrain, gis, risk,
│   │   │                           monitoring, dashboard, alerts, simulation
│   │   ├── services/               one module per source + engine, simulator, alerts
│   │   ├── database/               SQLite schema and repositories
│   │   ├── schemas/                Pydantic request/response contracts
│   │   ├── models/                 domain enums
│   │   └── config/                 settings and logging
│   ├── tests/                      135 tests
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── components/ pages/ maps/ charts/ services/ hooks/ types/ styles/
├── ml/
│   ├── features.py train.py evaluate.py models/
├── data/
│   ├── regions/     uttarakhand.json, himachal_pradesh.json
│   ├── scenarios/   normal, heavy_rain, flash_flood_warning, extreme_flash_flood
│   ├── config/      risk_weights.json
│   └── cache/       SQLite (generated)
├── .env.example
└── README.md
```

## Deploying to Vercel

The repository is Vercel-ready: the React build is served as static files and
the FastAPI app runs as a Python serverless function from `api/index.py`.

Two adaptations make the serverless model work, both in the repo already:

- **A bundled OpenStreetMap snapshot** (`data/seed/cache_seed.json.gz`, 1.7 MB).
  A region-wide Overpass query takes 12–16 s and rate-limits shared cloud IPs,
  and serverless has no persistent cache to hold the result. The snapshot is
  loaded into the ordinary cache at startup with its **original fetch
  timestamp**, so the UI still reports its real age. Weather, river discharge
  and rainfall climatology are never seeded — those are always fetched live.
  Refresh the snapshot any time with `python scripts/export_seed.py` against a
  warmed local backend.
- **A trimmed dependency set.** The root `requirements.txt` installs only the
  web stack. pandas, numpy and scikit-learn belong to `ml/` and are not needed
  to serve the API, which keeps the bundle small and the cold start fast.

Measured on a cold instance with an empty cache: boot 0.4 s, map layers 0.16 s,
risk map 4.2 s, dashboard 3.8 s — all inside the 60 s function limit.

```bash
git remote add origin https://github.com/<user>/<repo>.git
git push -u origin master
npx vercel --prod
```

Vercel needs no environment variables: none of the data sources uses an API key.

**A note on demos.** A live presentation is safer from `npm run preview` on the
presenting machine than from any free-tier host — no cold start, no shared-IP
rate limiting, and no dependency on venue wifi.
