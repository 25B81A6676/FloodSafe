import { useMemo, useState } from 'react'
import { AlertPanel } from '../components/AlertPanel'
import { AlertDevicePanel } from '../components/AlertDevicePanel'
import {
  Card,
  ErrorBox,
  RISK_COLOR,
  RISK_ORDER,
  RISK_SYMBOL,
  RiskPill,
  Skeleton,
  Spinner,
  fmt,
  fmtInt,
  timeAgo,
} from '../components/ui'
import { useAsync } from '../hooks/useApi'
import { RiskMap } from '../maps/RiskMap'
import { api } from '../services/api'
import type { RiskLevel } from '../types'

export function Authority({
  regionId,
  onSelectLocation,
  refreshTick,
}: {
  regionId: string
  onSelectLocation: (id: string) => void
  /** Bumped by the header refresh button; included in fetch deps. */
  refreshTick: number
}) {
  const authority = useAsync(() => api.authority(regionId), [regionId, refreshTick], { pollMs: 120_000 })
  const riskMap = useAsync(() => api.riskMap(regionId), [regionId, refreshTick])
  const layers = useAsync(() => api.mapLayers(regionId), [regionId, refreshTick])
  const [filter, setFilter] = useState<RiskLevel | 'ALL'>('ALL')
  const [selected, setSelected] = useState<string | null>(null)

  /* Same staleness rule as the summary: cells and vector layers from the
     scope the user just left must not be drawn over the scope they are
     looking at now. */
  const riskMapData = riskMap.data?.region_id === regionId ? riskMap.data : null
  const layerData = layers.data?.region_id === regionId ? layers.data : null

  /* useAsync keeps the previous payload while refetching so the page does not
     blank, which means that during a scope change `authority.data` still
     describes the scope the user just left. Rendering it would put another
     state's rows and markers on this state's map, so it is gated on the
     payload's own region id — the same check the location list uses. */
  const data = authority.data?.region.id === regionId ? authority.data : null

  const rows = useMemo(
    () => (data?.locations ?? []).filter((l) => filter === 'ALL' || l.risk_level === filter),
    [data, filter],
  )

  if (authority.error && !data) {
    return <ErrorBox error={authority.error} onRetry={authority.refresh} />
  }
  if (!data) {
    return (
      <div className="grid" style={{ gap: 'var(--space-sm)' }}>
        <Skeleton height={90} />
        {/* Registered devices do not depend on the risk summary, so they stay
            visible while a wide scope is still being assessed - waiting 30
            seconds to find out whether your phone registered is no use during
            a demonstration. */}
        <AlertDevicePanel refreshTick={refreshTick} />
        <Skeleton height={520} />
      </div>
    )
  }

  const infraCounts = data.infrastructure?.counts ?? {}
  const exposure = data.exposure

  /* One table, three meanings. At national scope a row is a state, at state
     scope a district, and below that an individual monitoring location — so the
     heading has to say which, or the counts are ambiguous. */
  const rowLabel = { state: 'States', district: 'Districts', location: 'Monitoring sites' }[
    data.row_kind ?? 'location'
  ]

  return (
    <div className="grid" style={{ gap: 'var(--space-sm)' }}>
      {/* headline counters */}
      <div className="stat-strip">
        <div className="stat">
          <div className="stat-num">{data.totals.monitoring_locations}</div>
          <div className="stat-lbl">{rowLabel}</div>
        </div>
        {RISK_ORDER.map((lvl) => (
          <div className="stat" key={lvl}>
            <div className="stat-num" style={{ color: RISK_COLOR[lvl] }}>
              <span aria-hidden style={{ fontSize: 'var(--text-sm)', marginRight: 'var(--space-2xs)' }}>
                {RISK_SYMBOL[lvl]}
              </span>
              {data.totals.distribution[lvl]}
            </div>
            <div className="stat-lbl">{lvl}</div>
          </div>
        ))}
        <div className="stat">
          <div className="stat-num" style={{ color: data.alerts.actionable ? 'var(--status-moderate)' : undefined }}>
            {data.alerts.actionable}
          </div>
          <div className="stat-lbl">Active advisories</div>
        </div>
      </div>

      {data.mode === 'SIMULATION' && (
        <div className="notice notice-sim">
          <strong>SIMULATION MODE.</strong> This command view is showing scenario
          {data.scenario_name ? ` “${data.scenario_name}”` : ''}, not live conditions. Reset the
          simulator on the Dashboard to return to live data.
        </div>
      )}

      <div className="dash-layout" style={{ gridTemplateColumns: 'minmax(0, 1.55fr) minmax(0, 1fr)' }}>
        <div className="col">
          <Card
            title="Regional risk map"
            icon="🗺"
            right={riskMap.loading ? <Spinner /> : undefined}
            bodyClass="flush"
          >
            <RiskMap
              riskMap={riskMapData}
              layers={layerData}
              locations={data.locations}
              selectedId={selected}
              onSelect={(id) => setSelected(id)}
              center={[data.region.center.latitude, data.region.center.longitude]}
              zoom={data.region.default_zoom}
              /* Frame the whole scope, so a national view shows the country
                 rather than whatever fits at a fixed zoom. */
              bounds={[
                [data.region.bbox.min_lat, data.region.bbox.min_lon],
                [data.region.bbox.max_lat, data.region.bbox.max_lon],
              ]}
              tall
            />
          </Card>

          <Card
            title="Monitoring locations"
            icon="📋"
            right={
              <select
                value={filter}
                onChange={(e) => setFilter(e.target.value as RiskLevel | 'ALL')}
                aria-label="Filter by risk level"
              >
                <option value="ALL">All levels ({data.locations.length})</option>
                {RISK_ORDER.map((lvl) => (
                  <option key={lvl} value={lvl}>
                    {lvl} ({data.totals.distribution[lvl]})
                  </option>
                ))}
              </select>
            }
            bodyClass="flush"
          >
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Location</th>
                    <th>District</th>
                    <th>Risk</th>
                    <th className="num">Score</th>
                    <th className="num">Rain now</th>
                    <th className="num">24 h</th>
                    <th className="num">Elev</th>
                    <th className="num">Slope</th>
                    <th>River</th>
                    <th className="num">To river</th>
                    <th>Primary driver</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((l) => (
                    <tr
                      key={l.location_id}
                      className={selected === l.location_id ? 'selected' : ''}
                      onClick={() => setSelected(l.location_id)}
                      onDoubleClick={() => onSelectLocation(l.location_id)}
                      /* Rows are actionable, so they must be reachable by keyboard:
                         Enter highlights, Shift+Enter opens in the dashboard. */
                      tabIndex={0}
                      aria-selected={selected === l.location_id}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          if (e.shiftKey) onSelectLocation(l.location_id)
                          else setSelected(l.location_id)
                        }
                      }}
                      title="Click or press Enter to highlight; double-click or Shift+Enter to open in the dashboard"
                    >
                      <td>
                        <strong>{l.name}</strong>
                      </td>
                      <td className="muted">{l.district ?? '—'}</td>
                      <td>
                        <RiskPill level={l.risk_level} />
                      </td>
                      <td className="num" style={{ color: RISK_COLOR[l.risk_level], fontWeight: 700 }}>
                        {l.risk_score}
                      </td>
                      <td className="num">{fmt(l.rainfall_mm_h, 1)}</td>
                      <td className="num">{fmt(l.rain_24h_mm, 1)}</td>
                      <td className="num">{fmtInt(l.elevation_m)}</td>
                      <td className="num">{fmt(l.slope_deg, 1)}°</td>
                      <td className="muted">{l.river_status ?? '—'}</td>
                      <td className="num">{fmtInt(l.river_distance_m)}</td>
                      <td className="muted small">{l.top_factor ?? '—'}</td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={11} className="empty">
                        No locations at this risk level.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </div>

        <div className="col">
          <AlertDevicePanel refreshTick={refreshTick} />

          <Card
            title="Active advisories"
            icon="🚨"
            right={<span className="badge badge-neutral">{data.alerts.highest_severity}</span>}
          >
            {data.alerts.actionable_alerts.length === 0 ? (
              <div className="empty">
                No advisories above INFO. All monitored locations are within routine conditions.
              </div>
            ) : (
              <div className="grid" style={{ gap: 'var(--space-xs)' }}>
                {data.alerts.actionable_alerts.slice(0, 6).map((a) => (
                  <div key={a.id} onClick={() => setSelected(a.location_id)} style={{ cursor: 'pointer' }}>
                    <div className="tiny muted" style={{ marginBottom: 'var(--space-2xs)' }}>
                      {a.location_name}
                    </div>
                    <AlertPanel alert={a} compact />
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card title="Rainfall summary" icon="🌧">
            <div className="metric-grid" style={{ margin: -13 }}>
              <div className="metric">
                <div className="metric-label">Peak intensity</div>
                <div className="metric-value">
                  {fmt(data.rainfall_summary.max_intensity_mm_h, 1)}
                  <small>mm/h</small>
                </div>
              </div>
              <div className="metric">
                <div className="metric-label">Peak 24 h total</div>
                <div className="metric-value">
                  {fmt(data.rainfall_summary.max_24h_mm, 1)}
                  <small>mm</small>
                </div>
              </div>
              <div className="metric">
                <div className="metric-label">Mean 24 h</div>
                <div className="metric-value">
                  {fmt(data.rainfall_summary.mean_24h_mm, 1)}
                  <small>mm</small>
                </div>
              </div>
              <div className="metric">
                <div className="metric-label">Peak forecast 24 h</div>
                <div className="metric-value">
                  {fmt(data.rainfall_summary.max_forecast_24h_mm, 1)}
                  <small>mm</small>
                </div>
              </div>
            </div>
            {data.rainfall_summary.wettest_location && (
              <div className="tiny faint mt14">
                Wettest: <strong>{data.rainfall_summary.wettest_location.name}</strong> at{' '}
                {data.rainfall_summary.wettest_location.rain_24h_mm} mm in 24 h.
              </div>
            )}
          </Card>

          <Card
            title="Exposed infrastructure"
            icon="🏥"
            right={
              data.infrastructure && (
                <span className="badge badge-neutral">{data.infrastructure.total} mapped</span>
              )
            }
          >
            {Object.keys(infraCounts).length === 0 ? (
              <div className="empty">OpenStreetMap infrastructure layer unavailable.</div>
            ) : (
              <>
                <div className="row wrap" style={{ gap: 'var(--space-2xs)' }}>
                  {Object.entries(infraCounts).map(([kind, n]) => (
                    <span key={kind} className="badge badge-neutral" style={{ textTransform: 'none' }}>
                      {kind}: <strong>{n}</strong>
                    </span>
                  ))}
                </div>
                {exposure && exposure.total > 0 ? (
                  <div className="notice notice-warn mt10">
                    <strong>
                      {exposure.total} facilities within {exposure.radius_km} km of a HIGH or
                      EXTREME location
                    </strong>
                    <div className="row wrap mt6" style={{ gap: 'var(--space-2xs)' }}>
                      {Object.entries(exposure.counts).map(([kind, n]) => (
                        <span key={kind} className="chip">
                          {kind} {n}
                        </span>
                      ))}
                    </div>
                    <div className="tiny mt6" style={{ opacity: 0.85 }}>
                      {exposure.note}
                    </div>
                  </div>
                ) : (
                  <div className="notice notice-info mt10">
                    No mapped facilities currently sit within {exposure?.radius_km ?? 10} km of a
                    HIGH or EXTREME location.
                  </div>
                )}
              </>
            )}
          </Card>

          <Card title="Feed status" icon="📡">
            {data.data_freshness.sources.map((s) => (
              <div className="source-row" key={s.name}>
                <div className="source-name">
                  <b>{s.label ?? s.name}</b>
                  <small>
                    {s.total_calls} calls · {s.total_failures} failures
                    {s.last_latency_ms != null ? ` · ${s.last_latency_ms} ms` : ''}
                  </small>
                </div>
                <span
                  className={`badge ${
                    s.status === 'OK'
                      ? 'badge-live'
                      : s.status === 'DEGRADED'
                        ? 'badge-stale'
                        : s.status === 'DOWN'
                          ? 'badge-demo'
                          : 'badge-neutral'
                  }`}
                >
                  <span className="dot" />
                  {s.status}
                </span>
              </div>
            ))}
            <div className="tiny faint mt10">Updated {timeAgo(data.generated_at)}.</div>
          </Card>
        </div>
      </div>

      <div className="tiny faint">{data.alerts.advisory_footer}</div>
    </div>
  )
}
