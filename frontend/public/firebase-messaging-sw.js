/* FloodSafe background messaging service worker.
 *
 * Firebase requires this file to sit at the origin root with exactly this name,
 * so it lives in public/ and is copied verbatim into the build. There is only
 * one service worker; do not add a competing one.
 *
 * It cannot import the app bundle and it cannot read Vite env vars, so it takes
 * its Firebase config from the query string the page registers it with.
 *
 * Messages are DATA-ONLY (see backend fcm_client.build_message), so this file is
 * the one place a backgrounded alert is rendered. With a `notification` payload
 * Firebase would show its own copy as well, which produced duplicates on Android.
 *
 * Honest limits, not worked around:
 *  - A service worker cannot play custom audio. With the page closed, the phone
 *    plays Chrome's notification sound for this site; on Android 8+ that sound,
 *    its volume and vibration are set by the site's notification channel in
 *    Android settings, which only the phone's owner can change.
 *  - `vibrate` below is honoured where the platform allows it and ignored where
 *    the notification channel governs vibration instead.
 *  - Nothing here can override silent mode or Do Not Disturb.
 *
 * Bump SW_VERSION whenever this file changes, so phones pick up the new worker.
 */
const SW_VERSION = 'floodsafe-sw-2'

importScripts('https://www.gstatic.com/firebasejs/10.14.1/firebase-app-compat.js')
importScripts('https://www.gstatic.com/firebasejs/10.14.1/firebase-messaging-compat.js')

const ICON = '/icons/floodsafe-192.png'
const BADGE = '/icons/floodsafe-badge-96.png'

// Kept in step with ALERT_VIBRATION in src/services/notifications.ts.
const VIBRATION = {
  EXTREME: [500, 200, 500, 200, 1000],
  HIGH: [300, 150, 300],
  TEST: [200, 100, 200],
}

/** Build notification options from a FloodSafe data message. */
function presentation(data) {
  const severity = data.severity || 'HIGH'
  const isExtreme = severity === 'EXTREME'
  const sentAt = Date.parse(data.sent_at || '')
  return {
    title: data.title || 'FloodSafe alert',
    options: {
      body: data.body || '',
      icon: ICON,
      badge: BADGE,
      // One notification per place and level: a repeat replaces rather than
      // stacks, and renotify makes the replacement alert again.
      tag: data.tag || 'floodsafe-alert',
      renotify: true,
      // EXTREME stays on screen until the user acts on it.
      requireInteraction: isExtreme || data.kind === 'SIMULATION',
      silent: false,
      vibrate: VIBRATION[severity] || VIBRATION.HIGH,
      timestamp: Number.isNaN(sentAt) ? Date.now() : sentAt,
      data: {
        click_path: data.click_path || '/',
        kind: data.kind || '',
        severity,
        location_id: data.location_id || '',
      },
      actions: [{ action: 'open', title: 'Open FloodSafe' }],
    },
  }
}

const params = new URL(self.location).searchParams
const config = {
  apiKey: params.get('apiKey') || '',
  authDomain: params.get('authDomain') || '',
  projectId: params.get('projectId') || '',
  messagingSenderId: params.get('messagingSenderId') || '',
  appId: params.get('appId') || '',
}

// Take over from an older worker immediately, so an updated notification
// format is not rendered by stale code during a demonstration.
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))

if (config.projectId && config.apiKey) {
  firebase.initializeApp(config)
  const messaging = firebase.messaging()

  messaging.onBackgroundMessage((payload) => {
    // Data-only messages carry everything in `data`. Older messages that still
    // carried a `notification` block are handled too.
    const data = { ...(payload.data || {}) }
    if (!data.title && payload.notification) {
      data.title = payload.notification.title
      data.body = payload.notification.body
    }
    const { title, options } = presentation(data)
    return self.registration.showNotification(title, options)
  })
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const path = (event.notification.data && event.notification.data.click_path) || '/'
  // Resolve against THIS worker's origin: the phone reached FloodSafe through a
  // tunnel or a deployment, never through the laptop's localhost.
  const target = new URL(path, self.location.origin).href
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (client.url.startsWith(self.location.origin) && 'focus' in client) {
          return client.navigate(target).then((c) => (c || client).focus())
        }
      }
      return self.clients.openWindow(target)
    }),
  )
})

// Exposed for the page's "is my worker current?" check.
self.addEventListener('message', (event) => {
  if (event.data === 'floodsafe-sw-version' && event.ports && event.ports[0]) {
    event.ports[0].postMessage(SW_VERSION)
  }
})
