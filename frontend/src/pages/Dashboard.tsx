import { useCallback, useEffect, useMemo, useState } from 'react'
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
import { RiskMap } from '../maps/RiskMap'
import { api } from '../services/api'
import type { DashboardSummary, MonitoringSnapshot } from '../types'

interface Props {
  regionId: string
  selectedLocation: string
  onSelectLocation: (id: string) => void
  summary: DashboardSummary | null
  summaryError: string | null
  onDataChanged: () => void
}

export function Dashboard({
  regionId,
  selectedLocation,
  onSelectLocation,
  summary,
  summaryError,
  onDataChanged,
}: Props) {
  const [busy, setBusy] = useState(false)

  const monitoring = useAsync<MonitoringSnapshot>(
    () => api.monitoring(selectedLocation),
    [selectedLocation],
    { enabled: Boolean(selectedLocation) },
  )

  const riskMap = useAsync(() => api.riskMap(regionId), [regionId])
  const layers = useAsync(() => api.mapLayers(regionId), [regionId])
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

  const runScenario = async (scenarioId: string) => {
    setBusy(true)
    try {
      const result = await api.runSimulation({ scenario_id: scenarioId, location_id: selectedLocation })
      if (result.monitoring) monitoring.setData(result.monitoring)
      riskMap.refresh()
      onDataChanged()
    } catch (e) {
      console.error('scenario run failed', e)
    } finally {
      setBusy(false)
    }
  }

  const applyOverrides = async (overrides: Record<string, number>) => {
    setBusy(true)
    try {
      const result = await api.runSimulation({ overrides, location_id: selectedLocation })
      if (result.monitoring) monitoring.setData(result.monitoring)
      riskMap.refresh()
      onDataChanged()
    } catch (e) {
      console.error('override failed', e)
    } finally {
      setBusy(false)
    }
  }

  const resetSimulation = async () => {
    setBusy(true)
    try {
      const result = await api.resetSimulation(selectedLocation)
      if (result.monitoring) monitoring.setData(result.monitoring)
      riskMap.refresh()
      onDataChanged()
    } catch (e) {
      console.error('reset failed', e)
    } finally {
      setBusy(false)
    }
  }

  // Keyboard shortcut: R refreshes, Esc leaves simulation.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return
      if (e.key === 'r' || e.key === 'R') refreshAll()
      if (e.key === 'Escape' && snap?.simulation.active) void resetSimulation()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshAll, snap?.simulation.active])

  if (monitoring.error && !snap) {
    return <ErrorBox error={monitoring.error} onRetry={monitoring.refresh} />
  }

  const center: [number, number] = snap
    ? [snap.location.latitude, snap.location.longitude]
    : summary
      ? [summary.region.center.latitude, summary.region.center.longitude]
      : [30.2, 79.0]

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
          title={`Risk map · ${summary?.region.name ?? regionId}`}
          icon="🗺"
          right={
            <div className="row" style={{ gap: 7 }}>
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
            riskMap={riskMap.data}
            layers={layers.data}
            locations={summary?.locations ?? []}
            selectedId={selectedLocation}
            onSelect={onSelectLocation}
            center={center}
            zoom={snap ? 10 : (summary?.region.default_zoom ?? 8)}
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
                Source: {snap.weather.source}. Bars left of “now” are observations; bars right are
                model forecast.
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
                {snap.timeline.method}
                {snap.risk.mode === 'SIMULATION' && (
                  <>
                    {' '}
                    The purple line is the active scenario. History is left as it actually
                    occurred — a simulation changes present conditions, not the past.
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
                {snap.hydrology.representative_cell.used_neighbour ? (
                  <>
                    Channel cell taken {fmtInt(snap.hydrology.representative_cell.distance_m)} m from
                    the settlement centroid; the centroid cell itself carries only{' '}
                    {fmt(snap.hydrology.representative_cell.centre_cell_mean_m3s, 2)} m³/s and is not
                    the river.
                  </>
                ) : (
                  <>Discharge read from the grid cell containing the settlement.</>
                )}
              </div>
            )}
          </Card>

          <Card title="Antecedent rainfall · 14 days" icon="💧">
            {snap ? (
              <>
                <AntecedentChart daily={snap.antecedent.recent_daily} />
                <div className="tiny faint mt6">{snap.antecedent.method}</div>
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
            active={snap?.simulation.active ?? false}
            activeScenarioId={snap?.simulation.scenario_id ?? null}
            overrides={snap?.simulation.overrides ?? {}}
            baseline={baseline}
            readouts={snap?.simulation.readouts ?? null}
            busy={busy}
            onRunScenario={runScenario}
            onOverride={applyOverrides}
            onReset={resetSimulation}
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
