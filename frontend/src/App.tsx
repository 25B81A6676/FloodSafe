import { useCallback, useEffect, useMemo, useState } from 'react'
import { FreshnessBadge, Spinner, timeAgo } from './components/ui'
import { useAsync, useStored } from './hooks/useApi'
import { Authority } from './pages/Authority'
import { Dashboard } from './pages/Dashboard'
import { Methodology } from './pages/Methodology'
import { api } from './services/api'
import type { DashboardSummary } from './types'

type Page = 'dashboard' | 'authority' | 'methodology'

export default function App() {
  const [page, setPage] = useStored<Page>('floodsafe.page', 'dashboard')
  const [regionId, setRegionId] = useStored<string>('floodsafe.region', 'uttarakhand')
  const [locationId, setLocationId] = useStored<string>('floodsafe.location', '')

  const regions = useAsync(() => api.regions(), [])
  const locations = useAsync(() => api.locations(regionId), [regionId])
  const sources = useAsync(() => api.sources(), [], { pollMs: 60_000 })

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
  }, [loadSummary])

  // Keep the selected location valid whenever the region changes.
  useEffect(() => {
    const list = locations.data?.locations ?? []
    if (list.length === 0) return
    if (!list.some((l) => l.id === locationId)) setLocationId(list[0].id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locations.data, locationId])

  const selectLocation = useCallback(
    (id: string) => {
      setLocationId(id)
      setPage('dashboard')
    },
    [setLocationId, setPage],
  )

  const simulationActive = summary?.simulation.active ?? false
  const degraded = useMemo(() => {
    const s = sources.data
    if (!s) return null
    if (!s.network_enabled) return 'Offline mode'
    return s.overall === 'OK' ? null : s.overall
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
            aria-current={page === 'dashboard'}
          >
            Dashboard
          </button>
          <button
            className={page === 'authority' ? 'active' : ''}
            onClick={() => setPage('authority')}
            aria-current={page === 'authority'}
          >
            Command centre
          </button>
          <button
            className={page === 'methodology' ? 'active' : ''}
            onClick={() => setPage('methodology')}
            aria-current={page === 'methodology'}
          >
            Methodology
          </button>
        </nav>

        <div className="header-right">
          {simulationActive && (
            <span className="badge badge-sim" title="The platform is showing a simulated scenario">
              <span className="dot" />
              Simulation active
            </span>
          )}

          {degraded && (
            <span className="badge badge-stale" title="One or more upstream data sources are unavailable">
              <span className="dot" />
              {degraded}
            </span>
          )}

          {sources.data && !degraded && (
            <FreshnessBadge freshness="LIVE" compact />
          )}

          <select
            value={regionId}
            onChange={(e) => setRegionId(e.target.value)}
            aria-label="Pilot region"
            title="Regions are pure configuration - add a JSON file to add a region"
          >
            {(regions.data?.regions ?? []).map((r) => (
              <option key={r.id} value={r.id}>
                {r.display_name}
              </option>
            ))}
          </select>

          {page !== 'methodology' && (
            <select
              value={locationId}
              onChange={(e) => setLocationId(e.target.value)}
              aria-label="Monitoring location"
              style={{ maxWidth: '13rem' }}
            >
              {(locations.data?.locations ?? []).map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                  {l.district ? ` · ${l.district}` : ''}
                </option>
              ))}
            </select>
          )}

          <button className="btn btn-sm" onClick={() => void loadSummary()} title="Refresh (R)">
            {summaryLoading ? <Spinner /> : '↻'}
          </button>
        </div>
      </header>

      <main className="main">
        {page === 'dashboard' && locationId && (
          <Dashboard
            regionId={regionId}
            selectedLocation={locationId}
            onSelectLocation={selectLocation}
            summary={summary}
            summaryError={summaryError}
            onDataChanged={loadSummary}
          />
        )}
        {page === 'authority' && <Authority regionId={regionId} onSelectLocation={selectLocation} />}
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
