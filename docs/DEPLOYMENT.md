# Deployment

FloodSafe runs on Vercel: the Vite build is served as static files, and the
FastAPI application runs as one Python function behind `/api`.

**Live:** https://floodsafe-rosy.vercel.app

---

## 1. What is where

| Piece | Where it runs |
|---|---|
| Dashboard, map, simulator | Static files from Vercel's CDN |
| API (`/api/*`, `/docs`) | One Python function, `api/index.py`, region `bom1` (Mumbai) |
| Cache database | SQLite in `/tmp` — **ephemeral**, see §4 |
| Device tokens | SQLite, mirrored to Firestore when enabled (§5) |

`vercel.json` holds the build command, the output directory, the function
settings and the rewrites. Three details in it are not obvious and are each
there for a reason:

* **`"framework": "vite"`** — without it Vercel decides the root FastAPI app is
  the whole project and stops serving the build. The symptom is the API's JSON
  index at `/`, and 404 for the service worker, icons and sounds.
* **`"regions": ["bom1"]`** — the default is Washington, which adds ~290 ms to
  every request from India.
* **The rewrites carry `__vercel_path`** — a Vercel rewrite replaces the request
  path with its destination, so without this every route arrives at FastAPI as
  `/api/index` and answers 404. A shim in `api/index.py` puts the path back.

The Python runtime is deliberately **not** pinned: a pinned version stops
matching the builder the platform installs and the build fails with
`pin-version-mismatch`.

## 2. Deploying

```bash
npx vercel login          # once, interactive
npx vercel --prod --yes
```

`.vercelignore` keeps the upload small. `data/cache/floodsafe.db` alone is
158 MB and is rebuilt at runtime, so it must never be uploaded.

## 3. Environment variables

Set on the Vercel project (Production). None of them belong in the repository.

| Variable | Purpose |
|---|---|
| `FIREBASE_WEB_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_WEB_PROJECT_ID`, `FIREBASE_MESSAGING_SENDER_ID`, `FIREBASE_APP_ID` | Firebase **client** config. Public by design — the browser sees all of it. |
| `FIREBASE_VAPID_KEY` | Web Push certificate public key. |
| `FCM_PROJECT_ID` | Firebase project that sends the pushes. |
| `FCM_SERVICE_ACCOUNT_JSON` | **Secret.** The whole service-account JSON. Never committed; paste it into Vercel. |
| `FLOOD_ALERT_TEST_MODE` | `true` (default): transitions in *real* measured risk are recorded but not pushed. Simulator and test alerts still send. |
| `DEVICE_STORE` | `firestore` to make registrations durable (§5). |
| `PUBLIC_DASHBOARD_URL` | The deployment's own URL, used in notification deep links. |

`google-auth[requests]` must stay in the root `requirements.txt`. Without the
`requests` extra, `google.auth.transport.requests` fails to import and every
push fails with *"could not obtain an FCM access token"* — while the build log
still shows google-auth installed.

## 4. Why it is fast, and what to do before a demonstration

A serverless instance starts with an empty cache, and the first request for a
state fetches weather, terrain and river geometry for every district in it.
Measured for one state's 33 district centres:

| Source | Cold |
|---|---|
| **River geometry (OpenStreetMap)** | **35.9 s** |
| Climatology | 2.2 s |
| Weather | 1.7 s |
| Hydrology | 1.2 s |
| Antecedent rainfall | 1.0 s |
| Terrain | 0.9 s |

Vercel kills a request at 60 s, so this was not slowness but gateway timeouts.
Two things fix it.

**The bundled snapshot.** River geometry barely changes, so it ships with the
repository and is loaded into the cache at startup:

```bash
python scripts/warm_osm_cache.py --skip-infrastructure   # fills the local cache
python scripts/export_seed.py                            # bundles it
```

Overpass rate-limits per IP, so the warmer is sequential and may need several
passes; it reports `INCOMPLETE` per state and keeps whatever it got. Verify
coverage before bundling — the script used to report success while caching
nothing, because the fetch degrades instead of raising.

The exporter skips entries over 1.5 MB. The state-wide facilities responses are
116 MB raw (Kerala alone is 39 MB); bundling them would slow the very cold
start this exists to fix. Those stay live.

**The CDN.** `backend/app/main.py` sets `s-maxage` and
`stale-while-revalidate` on responses that can be shared, so the edge answers
in about 0.1 s and refreshes behind the request. Never cached: notifications,
simulation, alerts, health, an explicit `?refresh=true`, and — while a
simulation is running — every endpoint carrying conditions, because a cached
map would show the pre-flood picture during the minute that matters.

**Before a demonstration**, populate the edge so the first visitor is not a
person in the room:

```bash
python scripts/warm_edge_cache.py --workers 2
```

Result, measured on the deployment:

| | Before | After |
|---|---|---|
| `/api/dashboard/summary` (new state) | 38 s | **0.11 s** |
| `/api/risk/map` | 60 s (timeout) | **0.08 s** |
| `/api/gis/layers` | 11 s | **0.32 s** |

## 5. Registered phones

Device tokens are the one thing that cannot be re-fetched: only the phone can
mint one. On this host SQLite lives in `/tmp` and is discarded whenever the
instance is recycled or a new version is deployed, so registrations disappear
and nothing visible happens until an alert fails to arrive.

Two independent defences:

* **The phone repairs itself.** It remembers what it registered for and
  re-asserts that on load, when the tab becomes visible, and every minute while
  open. Registration is an upsert keyed on the token, so a repeat costs one
  request. This needs no configuration and covers a demonstration, where the
  page is open on the phones anyway. It never prompts.
* **Firestore mirroring**, for registrations that must survive with every phone
  closed. Requires Cloud Firestore to be created once in the Firebase console
  (Build → Firestore Database → Create database → `asia-south1`), then
  `DEVICE_STORE=firestore`. The service account already in use is sufficient.
  Until then the app logs one warning and behaves exactly as before.

## 6. If something looks wrong

```bash
npx vercel logs https://floodsafe-rosy.vercel.app     # runtime errors
curl -s https://floodsafe-rosy.vercel.app/api/notifications/status
```

`serverReady: false` means FCM credentials are missing or unusable, and is
reported rather than hidden. `"FCM accepted"` means Firebase accepted the send
request — it is never a claim that a phone displayed anything.
