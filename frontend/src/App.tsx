import { useCallback, useEffect, useMemo, useState } from 'react'
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
  const [regionId, setRegionId] = useStored<string>('floodsafe.region', 'uttarakhand')
  const [storedLocationId, setStoredLocationId] = useStored<string>('floodsafe.location', '')

  /* One counter drives every manual refresh. Pages include it in their fetch
     deps, so one click fans out to exactly one refetch per query — no duplicate
     loops, and no page needs its own refresh plumbing. */
  const [refreshTick, setRefreshTick] = useState(0)

  const regions = useAsync(() => api.regions(), [])
  const locations = useAsync(() => api.locations(regionId), [regionId])
  const sources = useAsync(() => api.sources(), [refreshTick], { pollMs: 60_000 })

  /* The locations list lags a region change by one fetch (useAsync keeps the
     previous value so the UI doesn't blank). Comparing the payload's own
     region_id tells us whether the list on hand actually belongs to the region
     that is selected — without it the dashboard briefly requests a location
     from the region we just left. */
  const regionReady = locations.data?.region_id === regionId
  const locationList = useMemo(
    () => (regionReady ? (locations.data?.locations ?? []) : []),
    [regionReady, locations.data],
  )

  /* Derived during render rather than corrected by an effect, so it is never
     momentarily pointing at another region's location. */
  const locationId = useMemo(() => {
    if (locationList.length === 0) return ''
    return locationList.some((l) => l.id === storedLocationId)
      ? storedLocationId
      : locationList[0].id
  }, [locationList, storedLocationId])

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

  const loadSummary = useCallback(async () => {
    setSummaryLoading(true)
    try {
      const data = await api.dashboard(regionId)
      setSummary(data)
      setSummaryError(null)
    } catch (e) {
      setSummaryError(e instanceof Error ? e.message : String(e))
    } finally {
      setSummaryLoading(false)
    }
  }, [regionId])

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

          <label className="field field-region">
            <span className="field-label">Region</span>
            <select
              value={regionId}
              onChange={(e) => setRegionId(e.target.value)}
              aria-label="Pilot region"
            >
              {(regions.data?.regions ?? []).map((r) => (
                <option key={r.id} value={r.id}>
                  {r.display_name}
                </option>
              ))}
            </select>
          </label>

          {page !== 'methodology' && (
            <label className="field field-location">
              <span className="field-label">Location</span>
              <select
                value={locationId}
                onChange={(e) => setStoredLocationId(e.target.value)}
                aria-label="Monitoring location"
                disabled={!regionReady || locationList.length === 0}
              >
                {locationList.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                    {l.district ? ` · ${l.district}` : ''}
                  </option>
                ))}
              </select>
            </label>
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
      </header>

      <main className="main">
        {!regionReady && !locations.error && (
          <div className="empty">Loading {regions.data?.regions.find((r) => r.id === regionId)?.display_name ?? 'region'}…</div>
        )}

        {regionReady && page === 'dashboard' && locationId && (
          <Dashboard
            regionId={regionId}
            selectedLocation={locationId}
            onSelectLocation={selectLocation}
            summary={summary}
            summaryError={summaryError}
            onDataChanged={loadSummary}
            simulation={simulation}
            refreshTick={refreshTick}
          />
        )}
        {regionReady && page === 'authority' && (
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
