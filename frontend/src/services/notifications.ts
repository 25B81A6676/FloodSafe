/**
 * Flood-alert push notifications on this device.
 *
 * The Firebase SDK is imported lazily, so a build where nobody enables alerts
 * never pays for it and the app still starts when Firebase is unconfigured.
 *
 * Honest about platform limits: web push needs HTTPS (localhost excepted),
 * needs the user's permission, and cannot override silent mode or Do Not
 * Disturb. A custom sound only plays while the page is open — when it is not,
 * the browser owns the notification sound. None of that is worked around here.
 */
import { api } from './api'
import type { FirebaseConfig } from '../types'

const SOUND_URL = '/sounds/floodsafe-alert.wav'

/** Per-device presentation preferences. Deliberately local: they describe how
 *  THIS phone should behave, and the server has no business overriding them. */
export interface AlertPreferences {
  sound: boolean
  vibration: boolean
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

let audio: HTMLAudioElement | null = null

/** Play the FloodSafe tone. Returns false if the browser refused (autoplay). */
export async function playAlertSound(): Promise<boolean> {
  try {
    if (!audio) {
      audio = new Audio(SOUND_URL)
      audio.preload = 'auto'
    }
    audio.currentTime = 0
    await audio.play()
    return true
  } catch {
    // Browsers block audio until the user has interacted with the page. That
    // is a rule to respect, not to defeat; the notification still appears.
    return false
  }
}

export function vibrate(severity: string): boolean {
  if (!('vibrate' in navigator)) return false
  const pattern = severity === 'EXTREME' ? [300, 150, 300, 150, 600] : [250, 150, 250]
  try {
    return navigator.vibrate(pattern)
  } catch {
    return false
  }
}

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
  return navigator.serviceWorker.register(`/firebase-messaging-sw.js?${query.toString()}`)
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

  const config = await api.notificationConfig()
  if (!config.configured || !config.firebase.projectId || !config.vapidKey) {
    return {
      ok: false,
      reason:
        'Firebase is not configured on the server yet. See docs/NOTIFICATIONS.md — no push can be delivered until it is.',
    }
  }

  const permission = await Notification.requestPermission()
  if (permission !== 'granted') {
    return { ok: false, reason: `Notification permission was ${permission}.` }
  }

  const [{ initializeApp }, { getMessaging, getToken }] = await Promise.all([
    import('firebase/app'),
    import('firebase/messaging'),
  ])

  const app = initializeApp({
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

/**
 * Handle alerts that arrive while FloodSafe is open and visible.
 *
 * A foreground message does not raise a notification on its own, so the page
 * shows one and adds the FloodSafe tone and vibration itself, subject to the
 * user's own preferences.
 */
export async function listenForForegroundAlerts(
  onAlert: (payload: { title: string; body: string; severity: string }) => void,
): Promise<() => void> {
  if (!pushSupported() || permissionState() !== 'granted') return () => {}
  const config = await api.notificationConfig()
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

  return onMessage(getMessaging(app), (payload) => {
    const data = (payload.data ?? {}) as Record<string, string>
    const severity = data.severity ?? 'HIGH'
    const title = payload.notification?.title ?? 'FloodSafe alert'
    const body = payload.notification?.body ?? ''
    const prefs = loadPreferences()

    if (prefs.sound) void playAlertSound()
    if (prefs.vibration) vibrate(severity)
    try {
      new Notification(title, { body, icon: '/favicon.svg', tag: data.tag })
    } catch {
      /* some browsers only allow notifications from the service worker */
    }
    onAlert({ title, body, severity })
  })
}
