/* FloodSafe background messaging service worker.
 *
 * Firebase requires this file to sit at the origin root with exactly this name,
 * so it lives in public/ and is copied verbatim into the build.
 *
 * It cannot import the app bundle and it cannot read Vite env vars, so it takes
 * its Firebase config from the query string the page registers it with. That
 * keeps a single source of truth: the backend serves the config, the page
 * passes it here, and nothing is hardcoded into the repository.
 *
 * A service worker cannot reliably play custom audio - the browser owns the
 * notification sound when the page is not focused. The FloodSafe tone is played
 * by the page itself when it IS open; here we only ask for vibration, which
 * some platforms honour and others ignore.
 */
importScripts('https://www.gstatic.com/firebasejs/10.14.1/firebase-app-compat.js')
importScripts('https://www.gstatic.com/firebasejs/10.14.1/firebase-messaging-compat.js')

const params = new URL(self.location).searchParams
const config = {
  apiKey: params.get('apiKey') || '',
  authDomain: params.get('authDomain') || '',
  projectId: params.get('projectId') || '',
  messagingSenderId: params.get('messagingSenderId') || '',
  appId: params.get('appId') || '',
}

// Without a project id there is nothing to initialise; staying quiet is
// correct, and the page reports "not configured" rather than pretending.
if (config.projectId && config.apiKey) {
  firebase.initializeApp(config)
  const messaging = firebase.messaging()

  messaging.onBackgroundMessage((payload) => {
    const data = payload.data || {}
    const severity = data.severity || 'HIGH'
    const isExtreme = severity === 'EXTREME'
    const title = (payload.notification && payload.notification.title) || 'FloodSafe alert'
    const body = (payload.notification && payload.notification.body) || ''

    self.registration.showNotification(title, {
      body,
      icon: '/favicon.svg',
      badge: '/favicon.svg',
      tag: data.tag || 'floodsafe-alert',
      renotify: true,
      requireInteraction: isExtreme,
      vibrate: isExtreme ? [300, 150, 300, 150, 600] : [250, 150, 250],
      data: { click_url: data.click_url || '/', ...data },
    })
  })
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = (event.notification.data && event.notification.data.click_url) || '/'
  event.waitUntil(
    // Focus an already-open FloodSafe tab rather than opening a duplicate.
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.navigate(target)
          return client.focus()
        }
      }
      return self.clients.openWindow(target)
    }),
  )
})
