import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
// Tokens first: every rule in index.css references them by name.
import '../tokens.css'
import './styles/index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
