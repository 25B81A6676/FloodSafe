import { useState } from 'react'
import type { RiskAssessment } from '../types'

/**
 * Why the score is what it is.
 *
 * The bars are the model's actual weighted contributions - the same numbers
 * that produced the score - so the explanation cannot drift from the result.
 */
export function Explainability({ risk }: { risk: RiskAssessment }) {
  const [showAll, setShowAll] = useState(false)
  const rows = showAll ? risk.contributors : risk.contributors.slice(0, 6)
  const maxShare = Math.max(...risk.contributors.map((c) => c.share_pct), 1)

  return (
    <section className="card">
      <header className="card-head">
        <span aria-hidden>≡</span>
        <h3>Why this score</h3>
        <div className="spacer" />
        <button className="btn btn-ghost btn-sm" onClick={() => setShowAll((v) => !v)}>
          {showAll ? 'Top factors' : `All ${risk.contributors.length}`}
        </button>
      </header>

      <div className="card-body flush">
        {rows.map((c) => (
          <div className="contrib" key={c.key}>
            <div className="contrib-top">
              <span className="contrib-icon" aria-hidden>
                {c.icon}
              </span>
              <span className="contrib-name">{c.factor}</span>
              <span className="contrib-value">{c.display_value}</span>
            </div>

            <div
              className="contrib-bar"
              role="meter"
              aria-valuenow={Math.round(c.share_pct)}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`${c.factor}: ${c.impact} impact, ${c.share_pct.toFixed(1)} percent of the score`}
            >
              <div
                className={`contrib-bar-fill impact-${c.impact}`}
                style={{ width: `${(c.share_pct / maxShare) * 100}%` }}
              />
            </div>

            <div className="contrib-meta">
              <span className="chip">{c.impact} impact</span>
              <span className="mono">{c.share_pct.toFixed(1)}% of score</span>
              {c.weight != null && <span className="mono">weight {c.weight.toFixed(2)}</span>}
              {c.simulated && <span className="chip chip-sim">simulated</span>}
            </div>

            {c.detail && <div className="contrib-detail">{c.detail}</div>}
          </div>
        ))}
      </div>

      {risk.data_quality_notes.length > 0 && (
        <div className="card-body" style={{ borderTop: '1px solid var(--border)' }}>
          <div className="notice notice-info">
            <strong>Data quality</strong>
            <ul style={{ margin: '5px 0 0', paddingLeft: 17 }}>
              {risk.data_quality_notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </section>
  )
}
