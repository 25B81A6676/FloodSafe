import { useCallback, useEffect, useState } from 'react'
import {
  type AlertPreferences,
  enableAlerts,
  listenForForegroundAlerts,
  loadPreferences,
  permissionState,
  playAlertSound,
  pushSupported,
  savePreferences,
  secureContextOk,
  vibrate,
} from '../services/notifications'
import { Card, Spinner } from './ui'
import type { MonitoringLocation } from '../types'

/**
 * Per-device flood alert controls.
 *
 * Every claim here is one the browser can actually back: it says "registered",
 * not "you will receive alerts", because permission, OS notification settings,
 * silent mode and Do Not Disturb all sit between this and a phone buzzing.
 */
export function FloodAlerts({
  location,
  stateName,
  stateId,
}: {
  location: MonitoringLocation | null
  stateName?: string | null
  stateId?: string | null
}) {
  const [prefs, setPrefs] = useState<AlertPreferences>(() => loadPreferences())
  const [permission, setPermission] = useState(() => permissionState())
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ kind: 'ok' | 'warn'; text: string } | null>(null)
  const [lastAlert, setLastAlert] = useState<string | null>(null)

  const supported = pushSupported()
  const secure = secureContextOk()

  useEffect(() => {
    let stop: (() => void) | undefined
    void listenForForegroundAlerts(({ title, severity }) => {
      setLastAlert(`${severity} · ${title}`)
    }).then((fn) => {
      stop = fn
    })
    return () => stop?.()
  }, [])

  const update = useCallback((next: Partial<AlertPreferences>) => {
    setPrefs((current) => {
      const merged = { ...current, ...next }
      savePreferences(merged)
      return merged
    })
  }, [])

  const onEnable = useCallback(async () => {
    if (!location) return
    setBusy(true)
    setMessage(null)
    try {
      const result = await enableAlerts({
        location_id: location.id,
        location_name: location.name,
        district: location.district,
        state_id: stateId ?? null,
        state_name: stateName ?? null,
        latitude: location.latitude,
        longitude: location.longitude,
      })
      setPermission(permissionState())
      setMessage(
        result.ok
          ? { kind: 'ok', text: `Device registered for ${location.name}${result.deviceLabel ? ` as ${result.deviceLabel}` : ''}.` }
          : { kind: 'warn', text: result.reason ?? 'Could not enable alerts.' },
      )
    } catch (error) {
      setMessage({ kind: 'warn', text: error instanceof Error ? error.message : String(error) })
    } finally {
      setBusy(false)
    }
  }, [location, stateId, stateName])

  const onTestSound = useCallback(async () => {
    const played = await playAlertSound()
    if (prefs.vibration) vibrate('HIGH')
    if (!played) {
      setMessage({
        kind: 'warn',
        text: 'The browser blocked audio. Interact with the page once, then try again.',
      })
    }
  }, [prefs.vibration])

  return (
    <Card title="Flood alerts" icon="🔔">
      {!supported && (
        <p className="small muted">
          This browser does not support web push notifications. FloodSafe still works — only
          the phone alerting does not.
        </p>
      )}

      {supported && !secure && (
        <p className="small muted">
          Web push requires HTTPS. Open FloodSafe over <code>https://</code> (or on
          <code> localhost</code>) to register this device.
        </p>
      )}

      {supported && secure && (
        <>
          <div className="alert-prefs">
            <div className="alert-pref">
              <span>Notifications</span>
              <strong>{permission === 'granted' ? 'ON' : permission === 'denied' ? 'BLOCKED' : 'OFF'}</strong>
            </div>
            <label className="alert-pref">
              <span>Alert sound</span>
              <input
                type="checkbox"
                checked={prefs.sound}
                onChange={(e) => update({ sound: e.target.checked })}
              />
            </label>
            <label className="alert-pref">
              <span>Vibration</span>
              <input
                type="checkbox"
                checked={prefs.vibration}
                onChange={(e) => update({ vibration: e.target.checked })}
              />
            </label>
          </div>

          <div className="row" style={{ gap: 'var(--space-xs)', flexWrap: 'wrap' }}>
            <button className="btn btn-sm" onClick={() => void onEnable()} disabled={busy || !location}>
              {busy ? <Spinner /> : <span aria-hidden>🔔</span>}
              {permission === 'granted' ? 'Re-register this device' : 'Enable flood alerts'}
            </button>
            <button className="btn btn-sm" onClick={() => void onTestSound()}>
              <span aria-hidden>🔊</span> Test sound
            </button>
          </div>

          {permission === 'denied' && (
            <p className="small muted" style={{ marginTop: 'var(--space-2xs)' }}>
              Notification permission is blocked for this site. It has to be re-allowed in the
              browser's own site settings — a page cannot re-ask once it is denied.
            </p>
          )}

          {message && (
            <p
              className="small"
              style={{
                marginTop: 'var(--space-2xs)',
                color: message.kind === 'ok' ? 'var(--status-safe)' : 'var(--status-moderate)',
              }}
              role="status"
            >
              {message.kind === 'ok' ? '✅ ' : '⚠ '}
              {message.text}
            </p>
          )}

          {lastAlert && (
            <p className="small muted" style={{ marginTop: 'var(--space-2xs)' }}>
              Last alert received on this device: {lastAlert}
            </p>
          )}

          <p className="small muted" style={{ marginTop: 'var(--space-xs)' }}>
            Alerts are sent only to devices registered at the affected location, and only when
            risk transitions into HIGH or EXTREME. Delivery also depends on your phone's
            notification settings, silent mode and Do Not Disturb.
          </p>
        </>
      )}
    </Card>
  )
}
