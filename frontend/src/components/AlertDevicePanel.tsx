import { useCallback, useMemo, useState } from 'react'
import { useAsync } from '../hooks/useApi'
import { api } from '../services/api'
import { Card, Spinner, timeAgo } from './ui'
import type { AlertDispatch } from '../types'

const KIND_LABEL: Record<string, string> = {
  SIMULATION: 'SIMULATION',
  EMERGENCY: 'REAL',
  TEST: 'TEST',
}

/* Outcome wording for the Command Centre. "FCM accepted" is what the server
   knows; nothing here claims a phone displayed the notification. */
function dispatchOutcome(d: AlertDispatch): string {
  if (d.status === 'NO_TARGETS') return 'no devices registered at this location'
  if (d.status === 'SENDING') return 'sending…'
  return `targeted ${d.targeted} · FCM accepted ${d.accepted}` + (d.rejected > 0 ? ` · rejected ${d.rejected}` : '')
}

/**
 * Registered phones, for the Command Centre.
 *
 * Shows a masked token fragment, never the token itself — it is a bearer
 * credential for pushing to that phone. Dispatch outcomes are reported as
 * "FCM accepted", which is what the server actually knows; nothing here claims
 * a notification reached anyone's screen.
 */
export function AlertDevicePanel({ refreshTick }: { refreshTick: number }) {
  const status = useAsync(() => api.notificationStatus(), [refreshTick], { pollMs: 30_000 })
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)

  const data = status.data

  /* Registered phones grouped by place, so it is obvious at a glance which
     phones a simulation at a given location will reach. */
  const byLocation = useMemo(() => {
    const counts = new Map<string, number>()
    for (const d of data?.devices ?? []) {
      if (!d.active) continue
      const key = d.location_name ?? 'Unassigned'
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [data])

  const lastAlert = data?.recent_dispatches.find((d) => d.kind !== 'TEST') ?? null

  const sendTest = useCallback(async () => {
    setBusy(true)
    setResult(null)
    try {
      const r = await api.sendTestAlert()
      setResult(
        r.status === 'NOT_CONFIGURED'
          ? 'Firebase is not configured on the server, so nothing was sent.'
          : `${r.status} · targeted ${r.targeted} · FCM accepted ${r.accepted} · rejected ${r.rejected}`,
      )
      status.refresh()
    } catch (e) {
      setResult(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [status])

  if (!data) {
    return (
      <Card title="Flood alert devices" icon="📱">
        {status.error ? <p className="small muted">{status.error}</p> : <Spinner label="Loading devices…" />}
      </Card>
    )
  }

  return (
    <Card
      title="Flood alert devices"
      icon="📱"
      right={<span className="badge badge-neutral">{data.active_devices} active</span>}
    >
      {!data.configured && (
        <p className="small muted">
          Firebase Cloud Messaging is <strong>not configured</strong> on this server, so no push
          can be delivered. Devices can still register. See <code>docs/NOTIFICATIONS.md</code>.
        </p>
      )}

      {data.configured && data.test_mode && (
        <p className="small muted">
          <strong>Test mode is on.</strong> Transitions in <em>real</em> measured risk are recorded
          but not pushed. Simulator demo alerts and test alerts still send, labelled as
          demonstrations.
        </p>
      )}

      {byLocation.length > 0 && (
        <div className="device-summary">
          <span className="device-summary-total">
            Registered devices: <strong>{data.active_devices}</strong>
          </span>
          {byLocation.map(([place, count]) => (
            <span key={place} className="device-summary-place">
              {place}: <strong>{count}</strong>
            </span>
          ))}
        </div>
      )}

      {lastAlert && (
        <div className={`last-alert last-alert-${lastAlert.risk_level.toLowerCase()}`}>
          <div className="last-alert-head">
            Last alert:{' '}
            <strong>
              {lastAlert.risk_level === 'EXTREME' ? '🚨' : '⚠️'} {lastAlert.risk_level}{' '}
              {KIND_LABEL[lastAlert.kind] ?? lastAlert.kind}
            </strong>
          </div>
          <div>Location: {lastAlert.location_name ?? lastAlert.location_id}</div>
          <div className="mono">{dispatchOutcome(lastAlert)}</div>
          <div className="faint">{timeAgo(lastAlert.sent_at)}</div>
        </div>
      )}

      {data.devices.length === 0 ? (
        <p className="small muted">
          No devices registered yet. Open FloodSafe on a phone and use “Enable flood alerts”.
        </p>
      ) : (
        <ul className="device-list">
          {data.devices.map((d) => (
            <li key={d.id} className={d.active ? '' : 'device-inactive'}>
              <span aria-hidden>📱</span>
              <span className="device-name">{d.label ?? `Device ${d.id}`}</span>
              <span className="device-where">{d.location_name ?? 'unassigned'}</span>
              <code className="device-token">{d.token_hint}</code>
              <span className="device-seen">{timeAgo(d.last_seen_at)}</span>
              <button
                className="btn btn-sm"
                onClick={() => void api.setDeviceActive(d.id, !d.active).then(() => status.refresh())}
                title={d.active ? 'Stop sending alerts to this device' : 'Resume alerts to this device'}
              >
                {d.active ? 'Disable' : 'Enable'}
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="row" style={{ gap: 'var(--space-xs)', marginTop: 'var(--space-xs)' }}>
        <button className="btn btn-sm" onClick={() => void sendTest()} disabled={busy || data.active_devices === 0}>
          {busy ? <Spinner /> : <span aria-hidden>🧪</span>} Send test alert
        </button>
      </div>

      {result && (
        <p className="small" role="status" style={{ marginTop: 'var(--space-2xs)' }}>
          {result}
        </p>
      )}

      {data.recent_dispatches.length > 0 && (
        <>
          <h4 className="small" style={{ margin: 'var(--space-sm) 0 var(--space-2xs)' }}>
            Recent dispatches
          </h4>
          <ul className="dispatch-list">
            {data.recent_dispatches.slice(0, 5).map((d) => (
              <li key={d.id}>
                <strong>{d.risk_level}</strong>{' '}
                <span className="faint">{KIND_LABEL[d.kind] ?? d.kind}</span>{' '}
                {d.location_name ?? d.location_id}
                {' · '}
                {dispatchOutcome(d)}
                <span className="faint"> · {d.status.replace(/_/g, ' ').toLowerCase()}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      <p className="small muted" style={{ marginTop: 'var(--space-xs)' }}>
        {data.note}
      </p>
    </Card>
  )
}
