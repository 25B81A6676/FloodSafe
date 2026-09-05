import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ScopeBar } from './components/ScopeBar'
import { FreshnessBadge, Spinner, timeAgo } from './components/ui'
import { useAsync, useStored } from './hooks/useApi'
import { useSimulation } from './hooks/useSimulation'
import { Authority } from './pages/Authority'
import { Dashboard } from './pages/Dashboard'
import { Methodology } from './pages/Methodology'
import { api } from './services/api'
import type { DashboardSummary } from './types'

type Page = 'dashboard' | 'authority' | 'methodology'

export default function App() {
  const [page, setPage] = useStored<Page>('floodsafe.page', 'dashboard')
  const [stateId, setStateId] = useStored<string>('floodsafe.state', '')
  const [districtId, setDistrictId] = useStored<string>('floodsafe.district', '')
  const [storedLocationId, setStoredLocationId] = useStored<string>('floodsafe.location', '')

  /* One counter drives every manual refresh. Pages include it in their fetch
     deps, so one click fans out to exactly one refetch per query — no duplicate
     loops, and no page needs its own refresh plumbing. */
  const [refreshTick, setRefreshTick] = useState(0)

  /* The one scope the whole app is looking at. Every existing endpoint already
     takes a region_id, and the backend resolves 'india', a state slug and a
     district slug through the same path as a curated region — so this single
     derived value drives the dashboard, the map, the command centre and the
     risk grid without any of them knowing the hierarchy exists. */
  const regionId = districtId || stateId || 'india'

  const states = useAsync(() => api.states(), [])
  const districts = useAsync(
    () => api.districts(stateId),
    [stateId],
    { enabled: Boolean(stateId) },
  )
  const locations = useAsync(() => api.locations(regionId), [regionId])
  const sources = useAsync(() => api.sources(), [refreshTick], { pollMs: 60_000 })

  const stateList = states.data?.states ?? []
  const districtList = useMemo(
    () => (stateId && districts.data?.state_id === stateId ? districts.data.districts : []),
    [stateId, districts.data],
  )

  /* The locations list lags a scope change by one fetch (useAsync keeps the
     previous value so the UI doesn't blank). Comparing the payload's own
     region_id tells us whether the list on hand actually belongs to the scope
     that is selected — without it the dashboard briefly requests a location
     from the scope we just left. */
  const regionReady = locations.data?.region_id === regionId
  const locationList = useMemo(
    () => (regionReady ? (locations.data?.locations ?? []) : []),
    [regionReady, locations.data],
  )

  /* Derived during render rather than corrected by an effect, so it is never
     momentarily pointing at another region's location. */
  const locationId = useMemo(() => {
    if (locationList.length === 0) {
      /* Resolving a district's settlements means a live Overpass query, which
         takes tens of seconds the first time a district is opened. A district
         can be assessed from its own centroid straight away, so fall back to
         that rather than blanking the dashboard while OSM answers — the
         dropdown fills in behind it. The id is the one the backend already
         serves for a district centroid, so nothing here is invented. */
      return districtId ? `loc_${districtId}` : ''
    }
    return locationList.some((l) => l.id === storedLocationId)
      ? storedLocationId
      : locationList[0].id
  }, [locationList, storedLocationId, districtId])

  // Persist the corrected value so a reload starts where the user left off.
  useEffect(() => {
    if (locationId && locationId !== storedLocationId) setStoredLocationId(locationId)
  }, [locationId, storedLocationId, setStoredLocationId])

  /* The single authoritative simulation state. Header, dashboard and simulator
     panel all read this one object. */
  const simulation = useSimulation(locationId || null)

  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [summaryError, setSummaryError] = useState<string | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)

  /* Guards an out-of-order response. useAsync has this built in; this loader is
     hand-rolled, so without it a slow request for a scope the user has already
     left can land after a newer one and overwrite it. */
  const summaryScope = useRef(regionId)
  summaryScope.current = regionId

  const loadSummary = useCallback(async () => {
    const scope = regionId
    setSummaryLoading(true)
    try {
      const data = await api.dashboard(scope)
      if (summaryScope.current !== scope) return
      setSummary(data)
      setSummaryError(null)
    } catch (e) {
      if (summaryScope.current !== scope) return
      setSummaryError(e instanceof Error ? e.message : String(e))
    } finally {
      if (summaryScope.current === scope) setSummaryLoading(false)
    }
  }, [regionId])

  /* Centre and zoom for the scope that is selected RIGHT NOW, from geography
     the client already holds. The summary carries these too, but it arrives a
     fetch later, so relying on it alone left the map sitting on the previous
     scope — and, worse, drawing that scope's markers. */
  const scopeView = useMemo(() => {
    if (districtId) {
      const district = districtList.find((d) => d.id === districtId)
      return district ? { center: district.center, zoom: 9, name: district.name } : null
    }
    if (stateId) {
      const state = stateList.find((s) => s.id === stateId)
      return state ? { center: state.center, zoom: 7, name: state.name } : null
    }
    return { center: { latitude: 22.5, longitude: 79.0 }, zoom: 5, name: 'India' }
  }, [districtId, districtList, stateId, stateList])

  useEffect(() => {
    void loadSummary()
  }, [loadSummary, refreshTick])

  const refreshAll = useCallback(() => {
    setRefreshTick((n) => n + 1)
    void simulation.refresh()
  }, [simulation])

  const selectLocation = useCallback(
    (id: string) => {
      setStoredLocationId(id)
      setPage('dashboard')
    },
    [setStoredLocationId, setPage],
  )

  /* Changing one level invalidates every level below it. Clearing here rather
     than in an effect means the app never spends a render holding a district
     that does not belong to the selected state. */
  const selectState = useCallback(
    (id: string) => {
      setStateId(id)
      setDistrictId('')
      setStoredLocationId('')
    },
    [setStateId, setDistrictId, setStoredLocationId],
  )

  const selectDistrict = useCallback(
    (id: string) => {
      setDistrictId(id)
      setStoredLocationId('')
    },
    [setDistrictId, setStoredLocationId],
  )

  const exitSimulation = useCallback(async () => {
    await simulation.exit()
    setRefreshTick((n) => n + 1)
    void loadSummary()
  }, [simulation, loadSummary])

  // R refreshes from anywhere outside a form control.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return
      if (e.key === 'r' || e.key === 'R') refreshAll()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [refreshAll])

  const degraded = useMemo(() => {
    const s = sources.data
    if (!s) return null
    if (!s.network_enabled) return { label: 'Offline mode', detail: 'Outbound network is disabled; values are served from cache or clearly-labelled demo data.' }
    if (s.overall === 'OK') return null
    const down = s.sources.filter((x) => x.status === 'DOWN' || x.status === 'DEGRADED')
    return {
      label: s.overall === 'DOWN' ? 'Sources down' : 'Degraded',
      detail:
        `One or more external data sources are delayed or unavailable` +
        (down.length ? `: ${down.map((d) => d.label ?? d.name).join(', ')}. ` : '. ') +
        `Affected values fall back to cache or demo data and stay labelled as such.`,
    }
  }, [sources.data])

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <div className="brand-mark" aria-hidden>
            🌊
          </div>
          <div className="brand-text">
            <h1>FloodSafe</h1>
            <p>Flash Flood Monitoring &amp; Prediction</p>
          </div>
        </div>

        <nav className="nav" aria-label="Main">
          <button
            className={page === 'dashboard' ? 'active' : ''}
            onClick={() => setPage('dashboard')}
            aria-current={page === 'dashboard' ? 'page' : undefined}
          >
            Dashboard
          </button>
          <button
            className={page === 'authority' ? 'active' : ''}
            onClick={() => setPage('authority')}
            aria-current={page === 'authority' ? 'page' : undefined}
          >
            Command centre
          </button>
          <button
            className={page === 'methodology' ? 'active' : ''}
            onClick={() => setPage('methodology')}
            aria-current={page === 'methodology' ? 'page' : undefined}
          >
            Methodology
          </button>
        </nav>

        <div className="header-right">
          {/* Mode. When simulated, the way out sits immediately beside the badge. */}
          {simulation.active ? (
            <div className="mode-group" role="status" aria-live="polite">
              <span
                className="badge badge-sim"
                title="Risk values are being computed from simulator inputs, not measurements."
              >
                <span className="dot" />
                Simulation active
              </span>
              <button
                className="btn btn-sm btn-exit-sim"
                onClick={() => void exitSimulation()}
                disabled={simulation.busy}
                title="Leave simulation mode and restore live measured values"
              >
                {simulation.busy ? <Spinner /> : <span aria-hidden>✕</span>}
                Exit simulation
              </button>
            </div>
          ) : (
            sources.data && <FreshnessBadge freshness="LIVE" compact />
          )}

          {degraded && (
            <span className="badge badge-stale" title={degraded.detail} tabIndex={0}>
              <span className="dot" />
              {degraded.label}
            </span>
          )}

          <button
            className="btn btn-sm btn-refresh"
            onClick={refreshAll}
            disabled={summaryLoading}
            aria-label="Refresh all data"
            title="Re-fetch every panel on this page (keyboard: R)"
          >
            {summaryLoading ? <Spinner /> : <span aria-hidden>↻</span>}
            Refresh
          </button>
        </div>

        {page !== 'methodology' && (
          <ScopeBar
            states={stateList}
            districts={districtList}
            locations={locationList}
            stateId={stateId}
            districtId={districtId}
            locationId={locationId}
            onState={selectState}
            onDistrict={selectDistrict}
            onLocation={setStoredLocationId}
            locationsLoading={locations.loading && !regionReady}
            locationNote={locations.data?.notes?.[0] ?? null}
          />
        )}
      </header>

      <main className="main">
        {!regionReady && !locationId && !locations.error && (
          <div className="empty">
            Loading {locations.data?.region_name ?? stateList.find((s) => s.id === stateId)?.name ?? 'India'}…
          </div>
        )}
        {locations.error && <div className="empty">{locations.error}</div>}
        {states.error && (
          <div className="empty">
            Could not load the India geography dataset: {states.error}
          </div>
        )}

        {locationId && page === 'dashboard' && (
          <Dashboard
            regionId={regionId}
            selectedLocation={locationId}
            onSelectLocation={selectLocation}
            summary={summary}
            summaryError={summaryError}
            scopeView={scopeView}
            onDataChanged={loadSummary}
            simulation={simulation}
            refreshTick={refreshTick}
          />
        )}
        {(regionReady || locationId) && page === 'authority' && (
          <Authority
            regionId={regionId}
            onSelectLocation={selectLocation}
            refreshTick={refreshTick}
          />
        )}
        {page === 'methodology' && <Methodology />}
      </main>

      <footer className="app-footer">
        <div>
          <strong>FloodSafe</strong> — SIH26192 Flash Flood Prediction System for Hilly Regions
          using Multi-Source Data. Decision-support prototype: it does not issue official warnings
          or evacuation orders, and must not be the sole basis for emergency decisions.
        </div>
        <div style={{ marginTop: 'var(--space-2xs)' }}>
          Weather and elevation by <a href="https://open-meteo.com/" target="_blank" rel="noreferrer">Open-Meteo</a> (CC BY 4.0) ·
          River discharge from <a href="https://global-flood.emergency.copernicus.eu/" target="_blank" rel="noreferrer">Copernicus EMS GloFAS</a> ·
          Historical rainfall from ECMWF ERA5 ·
          Map data © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors (ODbL)
          {summary && <> · Updated {timeAgo(summary.generated_at)}</>}
        </div>
      </footer>
    </div>
  )
}
