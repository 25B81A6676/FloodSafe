import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { Hydrology, TimelinePoint, WeatherSeriesPoint } from '../types'
import { shortTime } from '../components/ui'
import { useDesignTokens, type DesignTokens } from '../hooks/useDesignTokens'

/* Every colour and size below comes from useDesignTokens, which resolves
   tokens.css. Nothing in this file declares a colour of its own. */

const axisProps = (t: DesignTokens) => ({ stroke: t.axis, fontSize: t.fontXs })

const tooltipProps = (t: DesignTokens) => ({
  contentStyle: {
    background: t.tooltipBg,
    border: `1px solid ${t.tooltipBorder}`,
    borderRadius: 6,
    fontSize: t.fontSm,
  },
  labelStyle: { color: t.tooltipLabel, fontSize: t.fontXs },
  itemStyle: { fontSize: t.fontSm },
  cursor: { fill: t.grid, fillOpacity: 0.35 },
})

const legendProps = (t: DesignTokens) => ({
  wrapperStyle: { fontSize: t.fontXs, paddingTop: 4, color: t.axis },
})

/** Observed hourly rainfall (past 24 h) and forecast rainfall (next 24 h). */
export function RainfallChart({
  past,
  forecast,
  height = 190,
}: {
  past: WeatherSeriesPoint[]
  forecast: WeatherSeriesPoint[]
  height?: number
}) {
  const t = useDesignTokens()
  const recent = past.slice(-24)
  const data = [
    ...recent.map((p) => ({
      time: shortTime(p.time),
      observed: p.precipitation ?? 0,
      forecast: null as number | null,
    })),
    ...forecast.map((p) => ({
      time: shortTime(p.time),
      observed: null as number | null,
      forecast: p.precipitation ?? 0,
      probability: p.probability ?? null,
    })),
  ]

  if (data.length === 0) return <div className="empty">No rainfall series available.</div>

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
        data={data}
        margin={{ top: 8, right: 12, left: 4, bottom: 4 }}
        role="img"
        aria-label="Hourly rainfall: observed over the past 24 hours and forecast for the next 24 hours, in millimetres per hour"
      >
        <CartesianGrid stroke={t.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="time" tick={axisProps(t)} interval={Math.max(3, Math.floor(data.length / 6))} minTickGap={18} tickLine={false} axisLine={{ stroke: t.grid }} />
        <YAxis
          tick={axisProps(t)} tickLine={false} axisLine={false} width={52}
          allowDecimals={false}
          label={{ value: 'mm per hour', angle: -90, position: 'insideLeft', fill: t.axis, fontSize: t.fontXs, style: { textAnchor: 'middle' } }}
        />
        <Tooltip {...tooltipProps(t)} formatter={(v) => (v == null ? '—' : `${v} mm`)} />
        <Legend {...legendProps(t)} />
        {recent.length > 0 && (
          <ReferenceLine
            x={data[recent.length - 1]?.time}
            stroke={t.marker}
            strokeDasharray="3 3"
            label={{ value: 'now', fill: t.marker, fontSize: t.fontXs, position: 'top' }}
          />
        )}
        <Bar dataKey="observed" name="Observed" fill={t.primary} radius={[2, 2, 0, 0]} maxBarSize={14} />
        <Bar dataKey="forecast" name="Forecast" fill={t.projected} fillOpacity={0.6} radius={[2, 2, 0, 0]} maxBarSize={14} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/**
 * Risk over time. Past points are the real model re-run over observed rainfall;
 * forward points are the same model over forecast rainfall.
 */
export function RiskTrendChart({
  past,
  forecast,
  simulatedScore,
  height = 200,
}: {
  past: TimelinePoint[]
  forecast: TimelinePoint[]
  /** When a scenario is active, drawn as a reference line against the real history. */
  simulatedScore?: number | null
  height?: number
}) {
  const t = useDesignTokens()
  const data = [
    ...past.map((p) => ({ time: shortTime(p.time), modelled: p.risk_score, projected: null as number | null })),
    ...forecast.map((p, i) => ({
      time: shortTime(p.time),
      // Repeat the join point so the two lines connect visually.
      modelled: i === 0 && past.length ? past[past.length - 1].risk_score : null,
      projected: p.risk_score,
    })),
  ]

  if (data.length === 0) return <div className="empty">No risk timeline available.</div>

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
        data={data}
        margin={{ top: 8, right: 12, left: 4, bottom: 4 }}
        role="img"
        aria-label="Flash-flood risk score over time, modelled from observed rainfall and projected from the forecast, on a scale of 0 to 100"
      >
        <defs>
          <linearGradient id="riskFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={t.primary} stopOpacity={0.3} />
            <stop offset="100%" stopColor={t.primary} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={t.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="time" tick={axisProps(t)} interval={Math.max(3, Math.floor(data.length / 6))} minTickGap={18} tickLine={false} axisLine={{ stroke: t.grid }} />
        <YAxis
          domain={[0, 100]} ticks={[0, 20, 40, 60, 80, 100]}
          tick={axisProps(t)} tickLine={false} axisLine={false} width={52}
          label={{ value: 'risk score', angle: -90, position: 'insideLeft', fill: t.axis, fontSize: t.fontXs, style: { textAnchor: 'middle' } }}
        />
        <Tooltip {...tooltipProps(t)} formatter={(v) => (v == null ? '—' : `${v} / 100`)} />
        <Legend {...legendProps(t)} />

        {/* Risk-class bands, so the line is readable without reading numbers. */}
        <ReferenceLine y={20} stroke={t.safe} strokeDasharray="2 5" strokeOpacity={0.5} />
        <ReferenceLine y={40} stroke={t.low} strokeDasharray="2 5" strokeOpacity={0.5} />
        <ReferenceLine y={60} stroke={t.moderate} strokeDasharray="2 5" strokeOpacity={0.5} />
        <ReferenceLine y={80} stroke={t.high} strokeDasharray="2 5" strokeOpacity={0.5} />
        {past.length > 0 && (
          <ReferenceLine x={data[past.length - 1]?.time} stroke={t.marker} strokeDasharray="3 3" label={{ value: 'now', fill: t.marker, fontSize: t.fontXs, position: 'top' }} />
        )}

        {/* A scenario cannot rewrite what actually happened, so the simulated
            score is drawn against the real history rather than replacing it. */}
        {simulatedScore != null && (
          <ReferenceLine
            y={simulatedScore}
            stroke={t.projected}
            strokeWidth={2}
            label={{
              value: `scenario ${simulatedScore}`,
              fill: t.scenarioLabel,
              fontSize: t.fontXs,
              position: 'insideTopLeft',
            }}
          />
        )}

        {/* Shading only — legendType none, or it repeats "modelled" in the legend. */}
        <Area type="monotone" dataKey="modelled" stroke="none" fill="url(#riskFill)" connectNulls legendType="none" />
        <Line type="monotone" dataKey="modelled" name="Modelled" stroke={t.primary} strokeWidth={2} dot={false} connectNulls />
        <Line type="monotone" dataKey="projected" name="Projected" stroke={t.projected} strokeWidth={2} strokeDasharray="5 3" dot={false} connectNulls />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/** GloFAS river discharge: 30 days observed plus a 7-day forecast. */
export function DischargeChart({ hydrology, height = 170 }: { hydrology: Hydrology; height?: number }) {
  const t = useDesignTokens()
  const data = hydrology.series.map((p) => ({
    date: p.date?.slice(5) ?? '',
    observed: p.kind === 'observed' ? p.discharge : null,
    forecast: p.kind === 'forecast' ? p.discharge : null,
  }))

  if (data.length === 0) {
    return <div className="empty">GloFAS river discharge unavailable for this location.</div>
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
        data={data}
        margin={{ top: 8, right: 12, left: 4, bottom: 4 }}
        role="img"
        aria-label="Modelled river discharge in cubic metres per second: 30 days observed and a 7-day forecast, against the 30-day mean"
      >
        <CartesianGrid stroke={t.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="date" tick={axisProps(t)} interval={Math.max(3, Math.floor(data.length / 6))} minTickGap={18} tickLine={false} axisLine={{ stroke: t.grid }} />
        <YAxis
          tick={axisProps(t)} tickLine={false} axisLine={false} width={58}
          label={{ value: 'cubic metres / sec', angle: -90, position: 'insideLeft', fill: t.axis, fontSize: t.fontXs, style: { textAnchor: 'middle' } }}
        />
        <Tooltip {...tooltipProps(t)} formatter={(v) => (v == null ? '—' : `${v} m³/s`)} />
        <Legend {...legendProps(t)} />
        {hydrology.mean_30d_m3s != null && (
          <ReferenceLine
            y={hydrology.mean_30d_m3s}
            stroke={t.marker}
            strokeDasharray="4 4"
            label={{ value: '30-day mean', fill: t.marker, fontSize: t.fontXs, position: 'insideTopRight' }}
          />
        )}
        <Line type="monotone" dataKey="observed" name="Observed" stroke={t.discharge} strokeWidth={2} dot={false} connectNulls />
        <Line type="monotone" dataKey="forecast" name="Forecast" stroke={t.projected} strokeWidth={2} strokeDasharray="5 3" dot={false} connectNulls />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/** Daily rainfall over the previous two weeks - the input to the API index. */
export function AntecedentChart({
  daily,
  height = 130,
}: {
  daily: { date: string; precipitation_mm: number }[]
  height?: number
}) {
  const t = useDesignTokens()
  if (!daily?.length) return <div className="empty">No antecedent rainfall available.</div>
  const data = daily.map((d) => ({ date: d.date.slice(5), mm: d.precipitation_mm }))
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
        data={data}
        margin={{ top: 8, right: 12, left: 4, bottom: 4 }}
        role="img"
        aria-label="Daily rainfall in millimetres over the previous 14 days, the input to the antecedent precipitation index"
      >
        <CartesianGrid stroke={t.grid} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="date" tick={axisProps(t)} interval={1} minTickGap={12} tickLine={false} axisLine={{ stroke: t.grid }} />
        <YAxis
          tick={axisProps(t)} tickLine={false} axisLine={false} width={52} allowDecimals={false}
          label={{ value: 'mm per day', angle: -90, position: 'insideLeft', fill: t.axis, fontSize: t.fontXs, style: { textAnchor: 'middle' } }}
        />
        <Tooltip {...tooltipProps(t)} formatter={(v) => `${v} mm`} />
        <Bar dataKey="mm" fill={t.primary} radius={[2, 2, 0, 0]} maxBarSize={16} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
