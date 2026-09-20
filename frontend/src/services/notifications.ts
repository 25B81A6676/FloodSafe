/**
 * Flood-alert push notifications on this device.
 *
 * The Firebase SDK is imported lazily, so a build where nobody enables alerts
 * never pays for it and the app still starts when Firebase is unconfigured.
 *
 * Honest about platform limits: web push needs HTTPS (localhost excepted),
 * needs the user's permission, and cannot override silent mode, Do Not Disturb
 * or the phone's notification volume. The FloodSafe tones only play while the
 * page is open — when it is not, the phone plays Chrome's own notification sound
 * for this site. None of that is worked around here.
 */
import { api } from './api'
import type { FirebaseConfig } from '../types'

const SOUND_URL = '/sounds/floodsafe-alert.wav'
const EXTREME_SOUND_URL = '/sounds/floodsafe-extreme-alert.wav'
const ICON = '/icons/floodsafe-192.png'
const BADGE = '/icons/floodsafe-badge-96.png'

/** Kept in step with VIBRATION in public/firebase-messaging-sw.js. */
export const ALERT_VIBRATION: Record<string, number[]> = {
  EXTREME: [500, 200, 500, 200, 1000],
  HIGH: [300, 150, 300],
  TEST: [200, 100, 200],
}

/** Per-device presentation preferences. Deliberately local: they describe how
 *  THIS phone should behave, and the server has no business overriding them. */
export interface AlertPreferences {
  sound: boolean
  vibration: boolean
}

export interface ForegroundAlert {
  title: string
  body: string
  severity: string
  kind: string
  locationName: string | null
  riskScore: string | null
  receivedAt: number
}

const PREF_KEY = 'floodsafe.alertPrefs'

export function loadPreferences(): AlertPreferences {
  try {
    const raw = window.localStorage.getItem(PREF_KEY)
    if (raw) return { sound: true, vibration: true, ...JSON.parse(raw) }
  } catch {
    /* private mode */
  }
  return { sound: true, vibration: true }
}

export function savePreferences(prefs: AlertPreferences): void {
  try {
    window.localStorage.setItem(PREF_KEY, JSON.stringify(prefs))
  } catch {
    /* private mode */
  }
}

export function permissionState(): NotificationPermission | 'unsupported' {
  if (typeof Notification === 'undefined') return 'unsupported'
  return Notification.permission
}

export function pushSupported(): boolean {
  return (
    typeof Notification !== 'undefined' &&
    'serviceWorker' in navigator &&
    typeof PushManager !== 'undefined'
  )
}

/** Web push requires a secure context. localhost is treated as secure. */
export function secureContextOk(): boolean {
  return window.isSecureContext || location.hostname === 'localhost' || location.hostname === '127.0.0.1'
}

/* ---------------------------------------------------------------- audio */

const audioCache = new Map<string, HTMLAudioElement>()

function audioFor(url: string): HTMLAudioElement {
  let el = audioCache.get(url)
  if (!el) {
    el = new Audio(url)
    el.preload = 'auto'
    audioCache.set(url, el)
  }
  return el
}

/**
 * Prepare the alert sounds during a real user gesture.
 *
 * Browsers only allow a page to play audio after the person has interacted
 * with it. Loading the tones on the first tap means a later alert can sound
 * without its own tap. This follows the autoplay policy rather than evading it:
 * it only takes effect because the user genuinely interacted with the page.
 */
export function primeAudio(): void {
  for (const url of [SOUND_URL, EXTREME_SOUND_URL]) audioFor(url).load()
}

async function play(url: string): Promise<boolean> {
  try {
    const el = audioFor(url)
    el.currentTime = 0
    el.volume = 1
    await el.play()
    return true
  } catch {
    // Blocked until the page has had a user interaction. The notification
    // itself still appears; only the page-played tone is missing.
    return false
  }
}

/** The standard FloodSafe tone (HIGH alerts and tests). */
export function playAlertSound(): Promise<boolean> {
  return play(SOUND_URL)
}

/** The original FloodSafe emergency tone, for EXTREME alerts. */
export function playEmergencySound(): Promise<boolean> {
  return play(EXTREME_SOUND_URL)
}

export function vibrate(severity: string): boolean {
  if (!('vibrate' in navigator)) return false
  try {
    return navigator.vibrate(ALERT_VIBRATION[severity] ?? ALERT_VIBRATION.HIGH)
  } catch {
    return false
  }
}

/* ------------------------------------------------------ service worker */

async function registerServiceWorker(config: FirebaseConfig): Promise<ServiceWorkerRegistration> {
  // The worker reads its Firebase config from the query string, because a
  // service worker cannot see the app bundle or its environment.
  const query = new URLSearchParams({
    apiKey: config.apiKey ?? '',
    authDomain: config.authDomain ?? '',
    projectId: config.projectId ?? '',
    messagingSenderId: config.messagingSenderId ?? '',
    appId: config.appId ?? '',
  })
  const registration = await navigator.serviceWorker.register(`/firebase-messaging-sw.js?${query.toString()}`)
  // Fetch a newer worker if one has been deployed since this phone last visited.
  void registration.update().catch(() => undefined)
  return registration
}

/** What to actually do when the browser will not grant notifications.
 *  A page cannot re-ask once permission is denied; only the phone's owner can
 *  change it, in two separate places on Android. */
export function permissionHelp(permission: NotificationPermission | 'unsupported'): string {
  if (permission === 'default') {
    return 'The permission prompt was dismissed. Tap the button again and choose Allow.'
  }
  return (
    'Notifications are blocked for this site. On the phone: (1) tap the icon left of the ' +
    'address bar → Permissions → Notifications → Allow (or Chrome ⋮ → Settings → Site settings → ' +
    'Notifications → find this site → Allow). (2) Android Settings → Apps → Chrome → ' +
    'Notifications → turn ON. Then reload this page and tap the button again.'
  )
}

export interface EnableResult {
  ok: boolean
  reason?: string
  deviceLabel?: string
}

/**
 * Ask for permission, obtain this device's FCM token, and register it with the
 * FloodSafe backend against the currently selected place.
 */
export async function enableAlerts(target: {
  location_id?: string
  location_name?: string
  district?: string | null
  state_id?: string | null
  state_name?: string | null
  latitude?: number
  longitude?: number
  label?: string
}): Promise<EnableResult> {
  if (!pushSupported()) {
    return { ok: false, reason: 'This browser does not support web push notifications.' }
  }
  if (!secureContextOk()) {
    return {
      ok: false,
      reason: 'Web push needs HTTPS. Open FloodSafe over https:// or on localhost.',
    }
  }

  /* Ask for permission FIRST, before any network request. Browsers only show
     the permission prompt while the tap that asked for it is still "active",
     and that window is a few seconds. Fetching the Firebase config first -
     through an HTTPS tunnel on a phone, often slowly - let the tap expire, and
     Android Chrome then answers "denied" without ever showing a prompt.
     If permission is already granted there is nothing to ask. */
  let permission: NotificationPermission = Notification.permission
  if (permission !== 'granted') {
    permission = await Notification.requestPermission()
  }
  if (permission !== 'granted') {
    return { ok: false, reason: permissionHelp(permission) }
  }

  // Still inside the same tap: prepare the alert tones as well.
  primeAudio()

  const config = await api.notificationConfig()
  if (!config.configured || !config.firebase.projectId || !config.vapidKey) {
    return {
      ok: false,
      reason:
        'Firebase is not configured on the server yet. See docs/NOTIFICATIONS.md — no push can be delivered until it is.',
    }
  }

  const [{ initializeApp, getApps }, { getMessaging, getToken }] = await Promise.all([
    import('firebase/app'),
    import('firebase/messaging'),
  ])

  const app = getApps().length
    ? getApps()[0]
    : initializeApp({
        apiKey: config.firebase.apiKey ?? '',
        authDomain: config.firebase.authDomain ?? '',
        projectId: config.firebase.projectId,
        messagingSenderId: config.firebase.messagingSenderId ?? '',
        appId: config.firebase.appId ?? '',
      })

  const registration = await registerServiceWorker(config.firebase)
  const messaging = getMessaging(app)
  const token = await getToken(messaging, {
    vapidKey: config.vapidKey,
    serviceWorkerRegistration: registration,
  })
  if (!token) {
    return { ok: false, reason: 'The browser did not return a registration token.' }
  }

  const result = await api.registerDevice({ fcm_token: token, ...target })
  return { ok: true, deviceLabel: result.device.label ?? undefined }
}

/** Show a system notification the way Android Chrome permits.
 *
 *  Android Chrome refuses `new Notification(...)` outright ("Illegal
 *  constructor"): pages must go through the service worker registration. The
 *  options mirror the service worker's, so a foreground alert looks the same as
 *  a background one. */
async function showSystemNotification(data: Record<string, string>): Promise<void> {
  const severity = data.severity ?? 'HIGH'
  const sentAt = Date.parse(data.sent_at ?? '')
  const registration = await navigator.serviceWorker.ready
  const options: NotificationOptions & Record<string, unknown> = {
    body: data.body ?? '',
    icon: ICON,
    badge: BADGE,
    tag: data.tag ?? 'floodsafe-alert',
    renotify: true,
    requireInteraction: severity === 'EXTREME' || data.kind === 'SIMULATION',
    silent: false,
    vibrate: data.kind === 'SIMULATION' ? ALERT_VIBRATION.EXTREME : (ALERT_VIBRATION[severity] ?? ALERT_VIBRATION.HIGH),
    timestamp: Number.isNaN(sentAt) ? Date.now() : sentAt,
    data: { click_path: data.click_path ?? '/', kind: data.kind ?? '', severity },
  }
  await registration.showNotification(data.title ?? 'FloodSafe alert', options)
}

/**
 * Handle alerts that arrive while FloodSafe is open and visible.
 *
 * When a FloodSafe tab is visible, Firebase delivers the push to the PAGE and
 * the service worker shows nothing — so if no page listener exists, the alert is
 * silently lost. This must therefore be installed once, app-wide, not inside one
 * page's component. It shows the system notification, plays the FloodSafe tone
 * and vibrates, subject to the user's own preferences.
 */
export async function listenForForegroundAlerts(
  onAlert: (alert: ForegroundAlert) => void,
): Promise<() => void> {
  if (!pushSupported() || permissionState() !== 'granted') return () => {}
  let config
  try {
    config = await api.notificationConfig()
  } catch {
    return () => {}
  }
  if (!config.configured || !config.firebase.projectId) return () => {}

  const [{ initializeApp, getApps }, { getMessaging, onMessage }] = await Promise.all([
    import('firebase/app'),
    import('firebase/messaging'),
  ])
  const app = getApps().length
    ? getApps()[0]
    : initializeApp({
        apiKey: config.firebase.apiKey ?? '',
        authDomain: config.firebase.authDomain ?? '',
        projectId: config.firebase.projectId,
        messagingSenderId: config.firebase.messagingSenderId ?? '',
        appId: config.firebase.appId ?? '',
      })

  // The page must use the same registration the token was issued against.
  await registerServiceWorker(config.firebase)

  return onMessage(getMessaging(app), (payload) => {
    const data: Record<string, string> = { ...((payload.data ?? {}) as Record<string, string>) }
    if (!data.title && payload.notification) {
      data.title = payload.notification.title ?? ''
      data.body = payload.notification.body ?? ''
    }
    const severity = data.severity ?? 'HIGH'
    const prefs = loadPreferences()

    /* Every simulator demo alert uses the emergency tone and the strong
       vibration, HIGH as well as EXTREME, so the demonstration always sounds
       like a warning. Test alerts keep the ordinary tone, which is how the two
       are told apart by ear. Real alerts: emergency tone for EXTREME only. */
    const emergency = severity === 'EXTREME' || data.kind === 'SIMULATION'
    if (prefs.sound) void (emergency ? playEmergencySound() : playAlertSound())
    if (prefs.vibration) vibrate(emergency ? 'EXTREME' : severity)
    void showSystemNotification(data).catch(() => undefined)

    onAlert({
      title: data.title ?? 'FloodSafe alert',
      body: data.body ?? '',
      severity,
      kind: data.kind ?? '',
      locationName: data.location_name ?? null,
      riskScore: data.risk_score ?? null,
      receivedAt: Date.now(),
    })
  })
}
