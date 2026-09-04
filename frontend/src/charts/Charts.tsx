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

const AXIS = { stroke: '#5b6f91', fontSize: 10 }
const GRID = '#22334f'

const tooltipStyle = {
  contentStyle: {
    background: '#121d33',
    border: '1px solid #2f4468',
    borderRadius: 8,
    fontSize: 12,
  },
  labelStyle: { color: '#b3c3dd', fontSize: 11 },
  itemStyle: { fontSize: 12 },
}

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
      <ComposedChart data={data} margin={{ top: 6, right: 8, left: -22, bottom: 0 }}>
        <CartesianGrid stroke={GRID} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="time" tick={AXIS} interval={Math.max(2, Math.floor(data.length / 8))} tickLine={false} axisLine={{ stroke: GRID }} />
        <YAxis tick={AXIS} tickLine={false} axisLine={false} width={44} label={{ value: 'mm/h', angle: -90, position: 'insideLeft', fill: '#5b6f91', fontSize: 10, offset: 14 }} />
        <Tooltip {...tooltipStyle} formatter={(v) => (v == null ? '—' : `${v} mm`)} />
        <Legend wrapperStyle={{ fontSize: 11, paddingTop: 4 }} />
        {recent.length > 0 && (
          <ReferenceLine
            x={data[recent.length - 1]?.time}
            stroke="#7d92b4"
            strokeDasharray="3 3"
            label={{ value: 'now', fill: '#7d92b4', fontSize: 9, position: 'top' }}
          />
        )}
        <Bar dataKey="observed" name="Observed" fill="#38bdf8" radius={[2, 2, 0, 0]} maxBarSize={14} />
        <Bar dataKey="forecast" name="Forecast" fill="#a855f7" fillOpacity={0.55} radius={[2, 2, 0, 0]} maxBarSize={14} />
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
      <ComposedChart data={data} margin={{ top: 6, right: 8, left: -22, bottom: 0 }}>
        <defs>
          <linearGradient id="riskFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#38bdf8" stopOpacity={0.35} />
            <stop offset="100%" stopColor="#38bdf8" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={GRID} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="time" tick={AXIS} interval={Math.max(2, Math.floor(data.length / 8))} tickLine={false} axisLine={{ stroke: GRID }} />
        <YAxis domain={[0, 100]} ticks={[0, 20, 40, 60, 80, 100]} tick={AXIS} tickLine={false} axisLine={false} width={44} />
        <Tooltip {...tooltipStyle} formatter={(v) => (v == null ? '—' : `${v} / 100`)} />
        <Legend wrapperStyle={{ fontSize: 11, paddingTop: 4 }} />

        {/* Risk-class bands, so the line is readable without reading numbers. */}
        <ReferenceLine y={20} stroke="#16a34a" strokeDasharray="2 5" strokeOpacity={0.55} />
        <ReferenceLine y={40} stroke="#eab308" strokeDasharray="2 5" strokeOpacity={0.55} />
        <ReferenceLine y={60} stroke="#f97316" strokeDasharray="2 5" strokeOpacity={0.55} />
        <ReferenceLine y={80} stroke="#dc2626" strokeDasharray="2 5" strokeOpacity={0.55} />
        {past.length > 0 && (
          <ReferenceLine x={data[past.length - 1]?.time} stroke="#7d92b4" strokeDasharray="3 3" label={{ value: 'now', fill: '#7d92b4', fontSize: 9, position: 'top' }} />
        )}

        {/* A scenario cannot rewrite what actually happened, so the simulated
            score is drawn against the real history rather than replacing it. */}
        {simulatedScore != null && (
          <ReferenceLine
            y={simulatedScore}
            stroke="#a855f7"
            strokeWidth={2}
            label={{
              value: `scenario ${simulatedScore}`,
              fill: '#d8b4fe',
              fontSize: 10,
              position: 'insideTopLeft',
            }}
          />
        )}

        <Area type="monotone" dataKey="modelled" stroke="none" fill="url(#riskFill)" connectNulls />
        <Line type="monotone" dataKey="modelled" name="Modelled" stroke="#38bdf8" strokeWidth={2} dot={false} connectNulls />
        <Line type="monotone" dataKey="projected" name="Projected" stroke="#a855f7" strokeWidth={2} strokeDasharray="5 3" dot={false} connectNulls />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/** GloFAS river discharge: 30 days observed plus a 7-day forecast. */
export function DischargeChart({ hydrology, height = 170 }: { hydrology: Hydrology; height?: number }) {
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
      <ComposedChart data={data} margin={{ top: 6, right: 8, left: -14, bottom: 0 }}>
        <CartesianGrid stroke={GRID} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="date" tick={AXIS} interval={Math.max(3, Math.floor(data.length / 7))} tickLine={false} axisLine={{ stroke: GRID }} />
        <YAxis tick={AXIS} tickLine={false} axisLine={false} width={54} label={{ value: 'm³/s', angle: -90, position: 'insideLeft', fill: '#5b6f91', fontSize: 10, offset: 18 }} />
        <Tooltip {...tooltipStyle} formatter={(v) => (v == null ? '—' : `${v} m³/s`)} />
        <Legend wrapperStyle={{ fontSize: 11, paddingTop: 4 }} />
        {hydrology.mean_30d_m3s != null && (
          <ReferenceLine
            y={hydrology.mean_30d_m3s}
            stroke="#7d92b4"
            strokeDasharray="4 4"
            label={{ value: '30-day mean', fill: '#7d92b4', fontSize: 9, position: 'insideTopRight' }}
          />
        )}
        <Line type="monotone" dataKey="observed" name="Observed" stroke="#22d3ee" strokeWidth={2} dot={false} connectNulls />
        <Line type="monotone" dataKey="forecast" name="Forecast" stroke="#a855f7" strokeWidth={2} strokeDasharray="5 3" dot={false} connectNulls />
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
  if (!daily?.length) return <div className="empty">No antecedent rainfall available.</div>
  const data = daily.map((d) => ({ date: d.date.slice(5), mm: d.precipitation_mm }))
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 4, right: 8, left: -24, bottom: 0 }}>
        <CartesianGrid stroke={GRID} strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="date" tick={AXIS} interval={1} tickLine={false} axisLine={{ stroke: GRID }} />
        <YAxis tick={AXIS} tickLine={false} axisLine={false} width={44} />
        <Tooltip {...tooltipStyle} formatter={(v) => `${v} mm`} />
        <Bar dataKey="mm" fill="#0ea5e9" radius={[2, 2, 0, 0]} maxBarSize={16} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
