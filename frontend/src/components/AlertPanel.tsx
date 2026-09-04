import type { Alert } from '../types'
import { timeAgo } from './ui'

const SEVERITY_ICON: Record<string, string> = {
  INFO: 'ℹ',
  ADVISORY: '⚑',
  WARNING: '⚠',
  CRITICAL: '⛔',
}

export function AlertPanel({ alert, compact = false }: { alert: Alert; compact?: boolean }) {
  return (
    <div className={`alert alert-${alert.severity}`} role={alert.severity === 'CRITICAL' ? 'alert' : 'status'}>
      <div className="alert-head">
        <span aria-hidden style={{ fontSize: 'var(--text-base)' }}>
          {SEVERITY_ICON[alert.severity]}
        </span>
        <span className="alert-headline">{alert.headline}</span>
        <span className="badge badge-neutral">{alert.severity}</span>
        {alert.is_simulation && (
          <span className="badge badge-sim">
            <span className="dot" />
            Simulated
          </span>
        )}
        <span className="spacer" style={{ marginLeft: 'auto' }} />
        <span className="tiny faint">{timeAgo(alert.issued_at)}</span>
      </div>

      <div className="alert-msg">{alert.message}</div>

      {alert.simulation_notice && (
        <div className="notice notice-sim mt10">{alert.simulation_notice}</div>
      )}

      {!compact && alert.drivers.length > 0 && (
        <div className="mt10">
          <div className="tiny muted" style={{ marginBottom: 'var(--space-2xs)', textTransform: 'uppercase', letterSpacing: 'var(--tracking-label)' }}>
            Driving factors
          </div>
          <div className="row wrap" style={{ gap: 'var(--space-2xs)' }}>
            {alert.drivers.map((d, i) => (
              <span key={i} className="badge badge-neutral badge-wrap" style={{ textTransform: 'none' }}>
                {d}
              </span>
            ))}
          </div>
        </div>
      )}

      {!compact && (
        <>
          <div className="tiny muted mt10" style={{ textTransform: 'uppercase', letterSpacing: 'var(--tracking-label)' }}>
            Recommended actions
          </div>
          <ul className="alert-actions">
            {alert.actions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </>
      )}

      <div className="alert-footer">{alert.advisory_footer}</div>
    </div>
  )
}
