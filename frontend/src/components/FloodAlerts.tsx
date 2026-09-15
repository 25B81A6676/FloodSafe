import { useCallback, useEffect, useState } from 'react'
import {
  type AlertPreferences,
  enableAlerts,
  loadPreferences,
  permissionHelp,
  permissionState,
  playAlertSound,
  playEmergencySound,
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
  /* Read live, not once at mount. The phone's owner changes this in Chrome or
     Android settings and then comes back to the tab; a value captured at load
     showed "Re-register" while notifications were in fact blocked. */
  const [permission, setPermission] = useState(() => permissionState())
  useEffect(() => {
    const sync = () => setPermission(permissionState())
    window.addEventListener('focus', sync)
    document.addEventListener('visibilitychange', sync)
    return () => {
      window.removeEventListener('focus', sync)
      document.removeEventListener('visibilitychange', sync)
    }
  }, [])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ kind: 'ok' | 'warn'; text: string } | null>(null)

  const supported = pushSupported()
  const secure = secureContextOk()

  /* Foreground alerts are handled app-wide in App (useForegroundAlerts), not
     here: this card only exists on the Dashboard, and an alert that arrives
     while a phone shows another page would otherwise be lost. */

  const update = useCallback((next: Partial<AlertPreferences>) => {
    setPrefs((current) => {
      const merged = { ...current, ...next }
      savePreferences(merged)
      return merged
    })
  }, [])

  const onEnable = useCallback(async () => {
    if (!location) return
    // Nothing may be awaited before enableAlerts asks for permission: the
    // prompt is only allowed while this tap is still active.
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
      // Tell the app-wide foreground listener it can attach now.
      if (result.ok) window.dispatchEvent(new Event('floodsafe:alerts-enabled'))
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

  const onTestSound = useCallback(
    async (severity: 'HIGH' | 'EXTREME') => {
      const played = await (severity === 'EXTREME' ? playEmergencySound() : playAlertSound())
      const buzzed = prefs.vibration ? vibrate(severity) : false
      setMessage(
        played
          ? {
              kind: 'ok',
              text:
                severity === 'EXTREME'
                  ? `Emergency tone played${buzzed ? ' with vibration' : prefs.vibration ? ' (this device did not vibrate)' : ''}. Raise media volume if it was quiet.`
                  : 'Alert tone played.',
            }
          : {
              kind: 'warn',
              text: 'The browser blocked audio. Tap the page once, then try again.',
            },
      )
    },
    [prefs.vibration],
  )

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
              {permission === 'granted' ? 'Register this device here' : 'Enable flood alerts'}
            </button>
            <button className="btn btn-sm" onClick={() => void onTestSound('HIGH')}>
              <span aria-hidden>🔈</span> Test sound
            </button>
            <button
              className="btn btn-sm btn-danger"
              onClick={() => void onTestSound('EXTREME')}
              title="Play FloodSafe's original EXTREME emergency tone on this device"
            >
              <span aria-hidden>🔊</span> Test emergency sound
            </button>
          </div>

          {permission === 'denied' && !message && (
            <p className="small" style={{ marginTop: 'var(--space-2xs)', color: 'var(--status-moderate)' }}>
              ⚠ {permissionHelp('denied')}
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
