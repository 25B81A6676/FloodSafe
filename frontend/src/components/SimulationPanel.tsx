import { useEffect, useMemo, useRef, useState } from 'react'
import type { Scenario, SimulationControl, SimulationReadouts } from '../types'

interface Props {
  controls: SimulationControl[]
  scenarios: Scenario[]
  active: boolean
  activeScenarioId: string | null
  overrides: Record<string, number>
  /** Real measured values, used as slider starting points when live. */
  baseline: Record<string, number>
  readouts: SimulationReadouts | null
  busy: boolean
  onRunScenario: (scenarioId: string) => void
  onOverride: (overrides: Record<string, number>) => void
  onReset: () => void
}

const SEVERITY_TINT: Record<string, string> = {
  baseline: 'var(--status-safe)',
  elevated: 'var(--status-low)',
  severe: 'var(--status-high)',
  extreme: 'var(--status-extreme)',
}

export function SimulationPanel({
  controls,
  scenarios,
  active,
  activeScenarioId,
  overrides,
  baseline,
  readouts,
  busy,
  onRunScenario,
  onOverride,
  onReset,
}: Props) {
  // Slider positions are local so dragging stays smooth; changes are debounced
  // before they reach the backend.
  const [values, setValues] = useState<Record<string, number>>({})
  const debounce = useRef<number | undefined>(undefined)
  const dragging = useRef(false)

  const effective = useMemo(() => {
    const out: Record<string, number> = {}
    for (const c of controls) {
      const v = overrides[c.key] ?? baseline[c.key] ?? c.min
      out[c.key] = Math.min(c.max, Math.max(c.min, Number(v)))
    }
    return out
  }, [controls, overrides, baseline])

  useEffect(() => {
    if (!dragging.current) setValues(effective)
  }, [effective])

  useEffect(() => () => window.clearTimeout(debounce.current), [])

  const handleChange = (key: string, value: number) => {
    dragging.current = true
    const next = { ...values, [key]: value }
    setValues(next)
    window.clearTimeout(debounce.current)
    debounce.current = window.setTimeout(() => {
      dragging.current = false
      onOverride({ ...next })
    }, 260)
  }

  const groups = useMemo(() => {
    const map = new Map<string, SimulationControl[]>()
    for (const c of controls) {
      if (!map.has(c.group)) map.set(c.group, [])
      map.get(c.group)!.push(c)
    }
    return [...map.entries()]
  }, [controls])

  const extreme = scenarios.find((s) => s.id === 'extreme_flash_flood')

  return (
    <section className="card">
      <header className="card-head">
        <span aria-hidden>⚙</span>
        <h3>Flash-flood simulator</h3>
        <div className="spacer" />
        {active ? (
          <span className="badge badge-sim">
            <span className="dot" />
            Active
          </span>
        ) : (
          <span className="badge badge-live">
            <span className="dot" />
            Live data
          </span>
        )}
      </header>

      <div className="card-body">
        <div className="scenario-grid">
          {scenarios.map((s) => (
            <button
              key={s.id}
              className={`scenario-btn ${activeScenarioId === s.id ? 'active' : ''}`}
              onClick={() => onRunScenario(s.id)}
              disabled={busy}
              title={s.description}
              style={
                activeScenarioId === s.id
                  ? { borderColor: SEVERITY_TINT[s.severity] ?? 'var(--state-sim)' }
                  : undefined
              }
            >
              <strong>{s.short_name}</strong>
              <span>expects {s.expected_risk_level.replace(/_/g, ' ').toLowerCase()}</span>
            </button>
          ))}
        </div>

        <div className="row" style={{ gap: 'var(--space-xs)', marginTop: 'var(--space-sm)' }}>
          <button
            className="btn btn-danger btn-block"
            onClick={() => extreme && onRunScenario(extreme.id)}
            disabled={busy || !extreme}
          >
            {busy ? <span className="spinner" /> : <span aria-hidden>⛈</span>}
            Simulate flash flood
          </button>
          <button className="btn btn-block" onClick={onReset} disabled={busy || !active}>
            Reset scenario
          </button>
        </div>

        {active && readouts && (
          <div className="notice notice-sim mt10">
            <div className="row between">
              <span>
                <strong>River level:</strong> {readouts.river_level_label}
              </span>
              <span className="mono">
                +{readouts.water_rise_rate_cm_per_hr.toFixed(1)} cm/h
              </span>
            </div>
            <div className="tiny" style={{ marginTop: 'var(--space-2xs)', opacity: 0.85 }}>
              {readouts.note}
            </div>
          </div>
        )}

        {groups.map(([group, items]) => (
          <div key={group}>
            <div className="slider-group-title">{group}</div>
            {items.map((c) => (
              <div className="slider-row" key={c.key}>
                <div className="slider-head">
                  <label className="slider-label" htmlFor={`sim-${c.key}`} title={c.help}>
                    {c.label}
                    {c.model_weight != null && (
                      <span className="faint mono" style={{ fontSize: 'var(--text-xs)', marginLeft: 'var(--space-2xs)' }}>
                        w={c.model_weight.toFixed(2)}
                      </span>
                    )}
                  </label>
                  <span className="slider-val">
                    {(values[c.key] ?? c.min).toFixed(c.step < 1 ? 1 : 0)} {c.unit}
                  </span>
                </div>
                <input
                  id={`sim-${c.key}`}
                  type="range"
                  min={c.min}
                  max={c.max}
                  step={c.step}
                  value={values[c.key] ?? c.min}
                  onChange={(e) => handleChange(c.key, Number(e.target.value))}
                  aria-label={`${c.label} in ${c.unit}`}
                />
              </div>
            ))}
          </div>
        ))}

        <div className="notice notice-info mt10">
          Every control is a real model input. Moving a slider changes the feature the risk
          engine reads - it does not scale the output score. Simulated values are marked
          <strong> SIMULATION</strong> everywhere they appear.
        </div>
      </div>
    </section>
  )
}
