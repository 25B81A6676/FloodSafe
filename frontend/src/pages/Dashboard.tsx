import { useCallback, useEffect, useMemo } from 'react'
import { AntecedentChart, DischargeChart, RainfallChart, RiskTrendChart } from '../charts/Charts'
import { AlertPanel } from '../components/AlertPanel'
import { Explainability } from '../components/Explainability'
import { RiskCard } from '../components/RiskCard'
import { SimulationPanel } from '../components/SimulationPanel'
import {
  Card,
  ErrorBox,
  FreshnessBadge,
  Metric,
  RISK_COLOR,
  Skeleton,
  Spinner,
  fmt,
  fmtInt,
  timeAgo,
} from '../components/ui'
import { useAsync } from '../hooks/useApi'
import type { SimulationState } from '../hooks/useSimulation'
import { RiskMap } from '../maps/RiskMap'
import { api } from '../services/api'
import type { DashboardSummary, LatLon, MonitoringSnapshot } from '../types'

interface Props {
  regionId: string
  selectedLocation: string
  onSelectLocation: (id: string) => void
  summary: DashboardSummary | null
  summaryError: string | null
  /** Centre/zoom for the CURRENT scope, known before any fetch returns. */
  scopeView: { center: LatLon; zoom: number; name: string } | null
  onDataChanged: () => void
  /** The one authoritative simulation state, owned by App. */
  simulation: SimulationState
  /** Bumped by the header refresh button; included in fetch deps. */
  refreshTick: number
}

export function Dashboard({
  regionId,
  selectedLocation,
  onSelectLocation,
  summary: summaryForAnyScope,
  summaryError,
  scopeView,
  onDataChanged,
  simulation,
  refreshTick,
}: Props) {

  const monitoring = useAsync<MonitoringSnapshot>(
    () => api.monitoring(selectedLocation),
    [selectedLocation, refreshTick],
    { enabled: Boolean(selectedLocation) },
  )

  const riskMap = useAsync(() => api.riskMap(regionId), [regionId, refreshTick])
  const layers = useAsync(() => api.mapLayers(regionId), [regionId, refreshTick])
  const scenarios = useAsync(() => api.scenarios(), [])
  const controls = useAsync(() => api.simulationControls(), [])

  const snap = monitoring.data

  // Slider starting points come from the real measured feature values.
  const baseline = useMemo(() => {
    const out: Record<string, number> = {}
    for (const c of snap?.risk.contributors ?? []) {
      if (c.value != null && !c.simulated) out[c.key] = c.value
    }
    return out
  }, [snap])

  const refreshAll = useCallback(() => {
    monitoring.refresh()
    riskMap.refresh()
    onDataChanged()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedLocation, regionId])

  /* All three mutations go through the shared simulation state, then refresh
     the panels that depend on it. Dashboard keeps no simulation flag of its
     own — that was the source of the header and panel disagreeing. */
  const afterSimulationChange = useCallback(() => {
    monitoring.refresh()
    riskMap.refresh()
    onDataChanged()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedLocation, regionId])

  const runScenario = async (scenarioId: string) => {
    await simulation.runScenario(scenarioId)
    afterSimulationChange()
  }

  const applyOverrides = async (overrides: Record<string, number>) => {
    await simulation.applyOverrides(overrides)
    afterSimulationChange()
  }

  const exitSimulation = async () => {
    await simulation.exit()
    afterSimulationChange()
  }

  // Keyboard shortcut: R refreshes, Esc leaves simulation.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return
      if (e.key === 'r' || e.key === 'R') refreshAll()
      if (e.key === 'Escape' && simulation.active) void exitSimulation()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshAll, simulation.active])

  if (monitoring.error && !snap) {
    return <ErrorBox error={monitoring.error} onRetry={monitoring.refresh} />
  }

  /* Same staleness rule as the summary: cells and vector layers from the
     scope the user just left must not be drawn over the scope they are
     looking at now. */
  const riskMapData = riskMap.data?.region_id === regionId ? riskMap.data : null
  const layerData = layers.data?.region_id === regionId ? layers.data : null

  /* A summary that belongs to a DIFFERENT scope must never be rendered. It
     feeds the map markers, the map title, the totals and the highest-risk
     line, so showing a stale one draws another state's stations on this
     state's map and mislabels every number beside it. */
  const summary = summaryForAnyScope?.region.id === regionId ? summaryForAnyScope : null

  /* The map shows the selected SCOPE. Zooming to the selected point makes sense
     once that point is a real place inside a district (or a curated pilot
     region), but at national or state scope the "location" is a centroid — a
     tight zoom onto one would hide the very thing the user asked to see. */
  const scope = summary?.region.scope ?? null
  const focusOnLocation = Boolean(snap) && (scope === 'district' || scope === 'region')

  const center: [number, number] =
    focusOnLocation && snap
      ? [snap.location.latitude, snap.location.longitude]
      : summary
        ? [summary.region.center.latitude, summary.region.center.longitude]
        : scopeView
          ? [scopeView.center.latitude, scopeView.center.longitude]
          : [22.5, 79.0]

  const mapZoom = focusOnLocation ? 10 : (summary?.region.default_zoom ?? scopeView?.zoom ?? 5)

  return (
    <div className="dash-layout">
      {/* ------------------------------------------------ left column */}
      <div className="col">
        {snap ? (
          <>
            <RiskCard risk={snap.risk} locationName={snap.location.name} />
            <AlertPanel alert={snap.alert} />
          </>
        ) : (
          <>
            <Skeleton height={210} />
            <Skeleton height={130} />
          </>
        )}

        {snap && <Explainability risk={snap.risk} />}
      </div>

      {/* ------------------------------------------------ centre column */}
      <div className="col">
        {snap && (
          <Card
            title="Conditions"
            right={
              snap.risk.mode === 'SIMULATION' ? (
                <span className="badge badge-sim" title="Values marked here are simulator inputs, not measurements">
                  <span className="dot" />
                  Scenario values
                </span>
              ) : (
                <FreshnessBadge
                  freshness={snap.weather.freshness}
                  ageMinutes={snap.weather.age_minutes}
                />
              )
            }
            bodyClass="flush"
          >
            <div className="metric-grid">
              <Metric
                label="Rainfall now"
                icon="🌧"
                value={fmt(snap.risk.context.rainfall.current_mm_h, 1)}
                unit="mm/h"
                note={
                  <>
                    3 h {fmt(snap.risk.context.rainfall.rain_3h, 1)} mm · 24 h{' '}
                    {fmt(snap.risk.context.rainfall.rain_24h, 1)} mm
                  </>
                }
              />
              <Metric
                label="Forecast 24 h"
                icon="📅"
                value={fmt(snap.risk.context.rainfall.forecast_24h, 1)}
                unit="mm"
                note={
                  <>
                    peak {fmt(snap.risk.context.rainfall.forecast_peak_mm_h, 1)} mm/h · trend{' '}
                    {snap.risk.context.rainfall.trend?.toLowerCase() ?? '—'}
                  </>
                }
              />
              <Metric
                label="Elevation"
                icon="🏔"
                value={fmtInt(snap.risk.context.terrain.elevation_m)}
                unit="m"
                note={
                  <>
                    slope {fmt(snap.risk.context.terrain.slope_deg, 1)}° ·{' '}
                    {snap.risk.context.terrain.terrain_class}
                  </>
                }
              />
              <Metric
                label="River status"
                icon="🌊"
                value={snap.risk.context.hydrology.river_status ?? '—'}
                color={
                  snap.risk.context.hydrology.river_status === 'CRITICAL'
                    ? RISK_COLOR.EXTREME
                    : snap.risk.context.hydrology.river_status === 'HIGH'
                      ? RISK_COLOR.HIGH
                      : undefined
                }
                note={
                  <>
                    {fmt(snap.risk.context.hydrology.discharge_m3s, 1)} m³/s ·{' '}
                    {fmt(snap.risk.context.hydrology.discharge_ratio, 2)}× 30-day mean
                  </>
                }
              />
              <Metric
                label="Soil saturation"
                icon="💧"
                value={snap.risk.context.soil.saturation_class ?? '—'}
                note={
                  <>
                    API {fmt(snap.risk.context.soil.api_mm, 1)} mm ·{' '}
                    {fmt(snap.risk.context.soil.total_14d_mm, 0)} mm in 14 d
                  </>
                }
              />
              <Metric
                label="Nearest watercourse"
                icon="〰"
                value={fmtInt(snap.risk.context.hydrology.river_distance_m)}
                unit="m"
                note={
                  snap.risk.context.hydrology.nearest_waterway
                    ? `${snap.risk.context.hydrology.nearest_waterway} (${snap.risk.context.hydrology.nearest_waterway_type})`
                    : 'unnamed watercourse'
                }
              />
            </div>
          </Card>
        )}

        <Card
          title={`Risk map · ${summary?.region.name ?? scopeView?.name ?? regionId}`}
          icon="🗺"
          right={
            <div className="row" style={{ gap: 'var(--space-xs)' }}>
              {riskMap.loading && <Spinner />}
              {riskMap.data && (
                <span className="badge badge-neutral">
                  {riskMap.data.grid.cell_count} cells
                </span>
              )}
              {layers.data && (
                <FreshnessBadge freshness={layers.data.waterway_freshness} compact />
              )}
            </div>
          }
          bodyClass="flush"
        >
          <RiskMap
            riskMap={riskMapData}
            layers={layerData}
            locations={summary?.locations ?? []}
            selectedId={selectedLocation}
            onSelect={onSelectLocation}
            center={center}
            zoom={mapZoom}
          />
        </Card>

        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(330px, 1fr))' }}>
          <Card
            title="Rainfall · observed and forecast"
            icon="🌧"
            right={snap && <FreshnessBadge freshness={snap.weather.freshness} ageMinutes={snap.weather.age_minutes} />}
          >
            {snap ? (
              <RainfallChart past={snap.weather.series.past} forecast={snap.weather.series.forecast} />
            ) : (
              <Skeleton height={190} />
            )}
            {snap && (
              <div className="tiny faint mt6">
                <strong>How much rain is falling, hour by hour.</strong> Left of the “now” line is
                rain that already fell; right of it is the forecast. Taller bar means heavier rain.
                The IMD calls more than 100 mm in one hour a cloudburst.
                <br />
                Vertical axis: millimetres per hour. Horizontal axis: time, 24-hour clock.
                Source: {snap.weather.source}.
              </div>
            )}
          </Card>

          <Card title="Flash-flood risk trend" icon="📈">
            {snap ? (
              <RiskTrendChart
                past={snap.timeline.past}
                forecast={snap.timeline.forecast}
                simulatedScore={snap.risk.mode === 'SIMULATION' ? snap.risk.risk_score : null}
              />
            ) : (
              <Skeleton height={200} />
            )}
            {snap && (
              <div className="tiny faint mt6">
                <strong>How the flood risk score has moved, and where it is heading.</strong> The
                solid line is the score recalculated over rain that actually fell; the dashed line
                runs the same calculation over the forecast. The faint horizontal lines mark the
                boundaries between Safe, Low, Moderate, High and Extreme.
                <br />
                Vertical axis: risk score out of 100. Horizontal axis: time, 24-hour clock.
                {snap.risk.mode === 'SIMULATION' && (
                  <>
                    {' '}
                    The bold purple line is the active scenario. History is left as it actually
                    happened — a simulation changes present conditions, not the past.
                  </>
                )}
              </div>
            )}
          </Card>

          <Card
            title="River discharge · GloFAS"
            icon="🌊"
            right={snap && <FreshnessBadge freshness={snap.hydrology.freshness} ageMinutes={snap.hydrology.age_minutes} />}
          >
            {snap ? <DischargeChart hydrology={snap.hydrology} /> : <Skeleton height={170} />}
            {snap && (
              <div className="tiny faint mt6">
                <strong>How much water the river is actually carrying.</strong> Solid line is the
                last 30 days, dashed is the next 7. The grey line is this river’s own 30-day
                average — well above it means the river is swollen. This comes from Copernicus
                GloFAS and is measured independently of the rainfall above, so when both rise
                together that is genuine corroboration.
                <br />
                Vertical axis: cubic metres per second. Horizontal axis: date.
                {snap.hydrology.representative_cell.used_neighbour && (
                  <>
                    {' '}
                    Reading taken {fmtInt(snap.hydrology.representative_cell.distance_m)} m away,
                    where the river channel actually runs.
                  </>
                )}
              </div>
            )}
          </Card>

          <Card title="Antecedent rainfall · 14 days" icon="💧">
            {snap ? (
              <>
                <AntecedentChart daily={snap.antecedent.recent_daily} />
                <div className="tiny faint mt6">
                  <strong>How wet the ground already is.</strong> Rain over the last two weeks —
                  recent days count for more than older ones. Saturated ground cannot absorb more,
                  so almost all new rain runs straight off into the rivers. The same storm is far
                  more dangerous on wet ground than on dry.
                  <br />
                  Vertical axis: millimetres per day. Horizontal axis: date.
                </div>
              </>
            ) : (
              <Skeleton height={130} />
            )}
          </Card>
        </div>
      </div>

      {/* ------------------------------------------------ right column */}
      <div className="col col-right">
        {controls.data && scenarios.data ? (
          <SimulationPanel
            controls={controls.data.controls}
            scenarios={scenarios.data.scenarios}
            active={simulation.active}
            activeScenarioId={simulation.scenarioId}
            overrides={simulation.overrides}
            baseline={baseline}
            readouts={simulation.readouts}
            busy={simulation.busy}
            onRunScenario={runScenario}
            onOverride={applyOverrides}
            onExit={exitSimulation}
          />
        ) : (
          <Skeleton height={320} />
        )}

        {snap && (
          <Card title="Data sources" icon="🔗">
            {snap.data_freshness.sources.map((s) => (
              <div className="source-row" key={s.label}>
                <div className="source-name">
                  <b>{s.label}</b>
                  <small title={s.source ?? ''}>{s.source ?? 'unavailable'}</small>
                </div>
                <FreshnessBadge freshness={s.freshness} ageMinutes={s.age_minutes} compact />
              </div>
            ))}
            <div className="tiny faint mt10">
              {snap.data_freshness.live} live · {snap.data_freshness.cached} cached ·{' '}
              {snap.data_freshness.degraded} degraded. Generated {timeAgo(snap.generated_at)}.
            </div>
            {snap.data_freshness.degraded > 0 && (
              <div className="notice notice-warn mt10">
                Some sources are unavailable and are shown as stale cache or demo data. Values
                marked <strong>Demo data</strong> are not measurements.
              </div>
            )}
          </Card>
        )}

        {summary && (
          <Card
            title="Region overview"
            icon="📍"
            right={<span className="badge badge-neutral">{summary.totals.monitoring_locations} sites</span>}
          >
            <div className="stat-strip" style={{ gridTemplateColumns: 'repeat(5, 1fr)' }}>
              {(['SAFE', 'LOW', 'MODERATE', 'HIGH', 'EXTREME'] as const).map((lvl) => (
                <div className="stat" key={lvl} title={lvl}>
                  <div className="stat-num" style={{ color: RISK_COLOR[lvl] }}>
                    {summary.totals.distribution[lvl]}
                  </div>
                  <div className="stat-lbl">{lvl.slice(0, 4)}</div>
                </div>
              ))}
            </div>
            {summary.highest_risk && (
              <div className="notice notice-info mt10">
                <strong>Highest risk:</strong> {summary.highest_risk.name} —{' '}
                {summary.highest_risk.risk_level} ({summary.highest_risk.risk_score}/100).
                <br />
                <span className="faint">Driver: {summary.highest_risk.top_factor}</span>
              </div>
            )}
            <div className="tiny faint mt10">
              Wettest site:{' '}
              {summary.rainfall_summary.wettest_location
                ? `${summary.rainfall_summary.wettest_location.name} (${summary.rainfall_summary.wettest_location.rain_24h_mm} mm/24 h)`
                : '—'}
            </div>
          </Card>
        )}

        {summaryError && <ErrorBox error={summaryError} />}
      </div>
    </div>
  )
}
