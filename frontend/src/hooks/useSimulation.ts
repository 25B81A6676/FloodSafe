import { useCallback, useEffect, useState } from 'react'
import { api } from '../services/api'
import type { SimulationReadouts } from '../types'

/**
 * The single authoritative simulation state for the whole frontend.
 *
 * The server owns simulation mode (it is persisted in SQLite and applied inside
 * the risk engine), so this hook mirrors `GET /api/simulation/state` and is the
 * only thing the UI reads. Previously the header badge read
 * `summary.simulation.active` while the dashboard and simulator panel read
 * `monitoring.simulation.active` — two independent fetches that could disagree,
 * leaving the badge and the panel out of step.
 *
 * Every mutation goes through here and refreshes the same state, so there is
 * exactly one place that can be right or wrong.
 */
export interface SimulationState {
  active: boolean
  scenarioId: string | null
  overrides: Record<string, number>
  readouts: SimulationReadouts | null
  /** True while a run/exit request is in flight. */
  busy: boolean
  refresh: () => Promise<void>
  runScenario: (scenarioId: string) => Promise<void>
  applyOverrides: (overrides: Record<string, number>) => Promise<void>
  /** Leaves simulation mode entirely and restores live values. */
  exit: () => Promise<void>
}

export function useSimulation(locationId: string | null): SimulationState {
  const [active, setActive] = useState(false)
  const [scenarioId, setScenarioId] = useState<string | null>(null)
  const [overrides, setOverrides] = useState<Record<string, number>>({})
  const [readouts, setReadouts] = useState<SimulationReadouts | null>(null)
  const [busy, setBusy] = useState(false)

  const apply = useCallback(
    (s: {
      active: boolean
      scenario_id: string | null
      overrides: Record<string, number>
      readouts?: SimulationReadouts | null
    }) => {
      setActive(s.active)
      setScenarioId(s.scenario_id)
      setOverrides(s.overrides ?? {})
      setReadouts(s.readouts ?? null)
    },
    [],
  )

  const refresh = useCallback(async () => {
    try {
      apply(await api.simulationState())
    } catch {
      /* leave the last known state rather than flapping to LIVE on a blip */
    }
  }, [apply])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const runScenario = useCallback(
    async (id: string) => {
      setBusy(true)
      try {
        apply(await api.runSimulation({ scenario_id: id, location_id: locationId ?? undefined }))
      } finally {
        setBusy(false)
      }
    },
    [apply, locationId],
  )

  const applyOverrides = useCallback(
    async (next: Record<string, number>) => {
      setBusy(true)
      try {
        apply(await api.runSimulation({ overrides: next, location_id: locationId ?? undefined }))
      } finally {
        setBusy(false)
      }
    },
    [apply, locationId],
  )

  const exit = useCallback(async () => {
    setBusy(true)
    try {
      await api.resetSimulation(locationId ?? undefined)
      apply({ active: false, scenario_id: null, overrides: {}, readouts: null })
    } finally {
      setBusy(false)
    }
  }, [apply, locationId])

  return { active, scenarioId, overrides, readouts, busy, refresh, runScenario, applyOverrides, exit }
}
