import type { RiskAssessment } from '../types'
import { ConfidenceBadge, RISK_COLOR, RISK_SYMBOL, timeAgo } from './ui'

const TREND_ICON: Record<string, string> = {
  INCREASING: '↑',
  DECREASING: '↓',
  STEADY: '→',
  UNKNOWN: '·',
}

export function RiskCard({ risk, locationName }: { risk: RiskAssessment; locationName: string }) {
  const color = RISK_COLOR[risk.risk_level] ?? '#64748b'
  const simulated = risk.mode === 'SIMULATION'

  return (
    <section className="card">
      <header className="card-head">
        <span aria-hidden>◎</span>
        <h3>Current flash-flood risk</h3>
        <div className="spacer" />
        <span className="tiny faint">{timeAgo(risk.timestamp)}</span>
      </header>

      <div className="risk-hero" style={{ ['--risk-color' as string]: color }}>
        <div className="risk-hero-top">
          <div style={{ minWidth: 0 }}>
            <div className="risk-level-name">
              <span aria-hidden style={{ fontSize: 20, marginRight: 6 }}>
                {RISK_SYMBOL[risk.risk_level]}
              </span>
              {risk.risk_level}
            </div>
            <div className="risk-sub">
              {locationName}
              {risk.trend !== 'UNKNOWN' && (
                <>
                  {' · '}
                  <span aria-hidden>{TREND_ICON[risk.trend]}</span> {risk.trend.toLowerCase()}
                  {risk.score_delta != null && risk.score_delta !== 0 && (
                    <span className="mono">
                      {' '}
                      ({risk.score_delta > 0 ? '+' : ''}
                      {risk.score_delta})
                    </span>
                  )}
                </>
              )}
            </div>
          </div>
          <div className="risk-score-box">
            <div className="risk-score">
              {risk.risk_score}
              <span> / 100</span>
            </div>
          </div>
        </div>

        <div
          className="risk-bar"
          role="meter"
          aria-valuenow={risk.risk_score}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`Flash flood risk score ${risk.risk_score} of 100, level ${risk.risk_level}`}
        >
          <div className="risk-bar-fill" style={{ width: `${risk.risk_score}%` }} />
        </div>
        <div className="risk-bar-ticks" aria-hidden>
          <span>0 safe</span>
          <span>20</span>
          <span>40</span>
          <span>60</span>
          <span>80</span>
          <span>100 extreme</span>
        </div>

        <div className="risk-meta">
          {simulated && (
            <span className="badge badge-sim badge-pulse">
              <span className="dot" />
              Simulated scenario
            </span>
          )}
          <ConfidenceBadge
            confidence={risk.confidence}
            note={risk.data_quality_notes.join(' ') || undefined}
          />
          <span
            className="badge badge-neutral"
            title={`${risk.model.name} v${risk.model.version}`}
          >
            {risk.feature_summary.available}/{risk.feature_summary.total} features
          </span>
          {risk.model.weight_coverage < 0.999 && (
            <span
              className="badge badge-stale"
              title="Some features were unavailable. The score is renormalised over what was present rather than treating missing data as zero."
            >
              {Math.round(risk.model.weight_coverage * 100)}% model coverage
            </span>
          )}
        </div>
      </div>
    </section>
  )
}
