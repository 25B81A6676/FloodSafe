import type { ReactNode } from 'react'
import type { Confidence, Freshness, RiskLevel } from '../types'

/* Risk is never communicated by colour alone. Each level carries a distinct
   glyph AND its written name.

   The glyphs are an ordinal fill ramp — empty circle through full circle —
   rather than five unrelated shapes. Severity reads as escalation even in
   greyscale, which matters because the palette's lightness is deliberately
   non-monotonic (yellow is intrinsically lighter than red). */
export const RISK_SYMBOL: Record<RiskLevel, string> = {
  SAFE: '○', // empty circle
  LOW: '◔', // quarter filled
  MODERATE: '◑', // half filled
  HIGH: '◕', // three-quarters filled
  EXTREME: '●', // full circle
}

/* Resolved from tokens.css so CSS stays the single source of truth for colour.
   These are kept in sync with the severity scale the backend serves in
   data/config/risk_weights.json. */
export const RISK_COLOR: Record<RiskLevel, string> = {
  SAFE: 'var(--status-safe)',
  LOW: 'var(--status-low)',
  MODERATE: 'var(--status-moderate)',
  HIGH: 'var(--status-high)',
  EXTREME: 'var(--status-extreme)',
}

export const RISK_ORDER: RiskLevel[] = ['SAFE', 'LOW', 'MODERATE', 'HIGH', 'EXTREME']

export function Card({
  title,
  icon,
  right,
  children,
  bodyClass = '',
}: {
  title?: string
  icon?: ReactNode
  right?: ReactNode
  children: ReactNode
  bodyClass?: string
}) {
  return (
    <section className="card">
      {title && (
        <header className="card-head">
          {icon && <span aria-hidden>{icon}</span>}
          <h3>{title}</h3>
          {right && <div className="spacer" />}
          {right}
        </header>
      )}
      <div className={`card-body ${bodyClass}`}>{children}</div>
    </section>
  )
}

/* No pulse/blink state. The data is polled and cached, not streamed, so an
   animated 'live' indicator would overstate what the feed actually does. */
const FRESHNESS_META: Record<Freshness, { cls: string; label: string }> = {
  LIVE: { cls: 'badge-live', label: 'Live' },
  CACHED: { cls: 'badge-cached', label: 'Cached' },
  STALE_CACHE: { cls: 'badge-stale', label: 'Stale cache' },
  DEMO: { cls: 'badge-demo', label: 'Demo data' },
  SIMULATION: { cls: 'badge-sim', label: 'Simulation' },
}

export function FreshnessBadge({
  freshness,
  ageMinutes,
  compact = false,
}: {
  freshness: Freshness
  ageMinutes?: number | null
  compact?: boolean
}) {
  const meta = FRESHNESS_META[freshness] ?? FRESHNESS_META.DEMO
  const age =
    ageMinutes == null || compact
      ? null
      : ageMinutes < 1
        ? 'just now'
        : ageMinutes < 60
          ? `${Math.round(ageMinutes)} min ago`
          : `${(ageMinutes / 60).toFixed(1)} h ago`

  return (
    <span
      className={`badge ${meta.cls}`}
      title={`Data state: ${meta.label}${age ? ` - updated ${age}` : ''}`}
    >
      <span className="dot" />
      {meta.label}
      {age && <span style={{ opacity: 0.75, fontWeight: 500 }}>· {age}</span>}
    </span>
  )
}

export function RiskPill({ level, score }: { level: RiskLevel; score?: number }) {
  return (
    <span className="risk-pill" style={{ background: RISK_COLOR[level] }}>
      <span className="sym" aria-hidden>
        {RISK_SYMBOL[level]}
      </span>
      {level}
      {score != null && <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)' }}>{score}</span>}
    </span>
  )
}

export function ConfidenceBadge({ confidence, note }: { confidence: Confidence; note?: string }) {
  const cls =
    confidence === 'HIGH' ? 'badge-live' : confidence === 'MEDIUM' ? 'badge-cached' : 'badge-stale'
  return (
    <span
      className={`badge ${cls}`}
      title={note ?? 'Confidence describes data quality, not the probability of a flood.'}
    >
      {confidence} data confidence
    </span>
  )
}

export function Metric({
  label,
  icon,
  value,
  unit,
  note,
  color,
}: {
  label: string
  icon?: string
  value: ReactNode
  unit?: string
  note?: ReactNode
  color?: string
}) {
  return (
    <div className="metric">
      <div className="metric-label">
        {icon && <span aria-hidden>{icon}</span>}
        {label}
      </div>
      <div className="metric-value" style={color ? { color } : undefined}>
        {value}
        {unit && <small>{unit}</small>}
      </div>
      {note && <div className="metric-note">{note}</div>}
    </div>
  )
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row" role="status">
      <span className="spinner" />
      {label && <span className="small muted">{label}</span>}
    </span>
  )
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  const offline = error.toLowerCase().includes('backend')
  return (
    <div className="error-box">
      <h3>Could not load data</h3>
      <p style={{ margin: '0 0 var(--space-2xs)' }}>{error}</p>
      {offline && (
        <>
          <p className="small" style={{ margin: 'var(--space-xs) 0 0' }}>
            Start the backend from the project root:
          </p>
          <code>cd backend &amp;&amp; python -m uvicorn app.main:app --port 8000</code>
        </>
      )}
      {onRetry && (
        <div className="mt10">
          <button className="btn btn-sm" onClick={onRetry}>
            Retry
          </button>
        </div>
      )}
    </div>
  )
}

export function Skeleton({ height = 80 }: { height?: number }) {
  return <div className="skeleton" style={{ height }} />
}

export function fmt(value: number | null | undefined, digits = 1, fallback = '—'): string {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function fmtInt(value: number | null | undefined, fallback = '—'): string {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback
  return Math.round(value).toLocaleString()
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const secs = Math.max(0, (Date.now() - then) / 1000)
  if (secs < 45) return 'just now'
  if (secs < 3600) return `${Math.round(secs / 60)} min ago`
  if (secs < 86400) return `${Math.round(secs / 3600)} h ago`
  return `${Math.round(secs / 86400)} d ago`
}

/** Compact 24-hour label for chart axes. A locale 12-hour string ("04:00 PM")
 *  is nearly twice as wide and made the time axis collide with itself. */
export function shortTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(11, 16)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
