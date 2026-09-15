import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
// Tokens first: every rule in index.css references them by name.
import '../tokens.css'
import './styles/index.css'

/* Deep link from a tapped flood-alert notification: /?state=..&district=..&location=..
   Written into the same storage the scope selector already restores from,
   BEFORE the first render, so the app opens straight onto the alerted place
   instead of flashing the previous selection first. The query is then removed
   so a later refresh does not keep forcing it. */
;(() => {
  const params = new URLSearchParams(window.location.search)
  const location = params.get('location')
  if (!location) return
  try {
    window.localStorage.setItem('floodsafe.state', params.get('state') ?? '')
    window.localStorage.setItem('floodsafe.district', params.get('district') ?? '')
    window.localStorage.setItem('floodsafe.location', location)
    window.localStorage.setItem('floodsafe.page', 'dashboard')
  } catch {
    /* private mode: the app still opens, just on its default scope */
  }
  window.history.replaceState(null, '', window.location.pathname)
})()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
