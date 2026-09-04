import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../services/api'

export interface AsyncState<T> {
  data: T | null
  error: string | null
  loading: boolean
  /** True only for the first load, so refreshes do not blank the UI. */
  initialLoading: boolean
  refresh: () => void
  setData: (value: T) => void
}

/**
 * Fetch-with-refresh hook.
 *
 * Keeps the previous value visible while refetching, so a dashboard that is
 * polling never flickers between states during an incident.
 */
export function useAsync<T>(
  fetcher: () => Promise<T>,
  deps: unknown[],
  options: { pollMs?: number; enabled?: boolean } = {},
): AsyncState<T> {
  const { pollMs, enabled = true } = options
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [initialLoading, setInitialLoading] = useState(true)

  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  const mounted = useRef(true)
  const generation = useRef(0)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const run = useCallback(async () => {
    const gen = ++generation.current
    setLoading(true)
    try {
      const result = await fetcherRef.current()
      if (!mounted.current || gen !== generation.current) return
      setData(result)
      setError(null)
    } catch (e) {
      if (!mounted.current || gen !== generation.current) return
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      if (mounted.current && gen === generation.current) {
        setLoading(false)
        setInitialLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    if (!enabled) {
      setInitialLoading(false)
      return
    }
    void run()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled])

  useEffect(() => {
    if (!pollMs || !enabled) return
    const id = window.setInterval(() => void run(), pollMs)
    return () => window.clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pollMs, enabled, ...deps])

  return { data, error, loading, initialLoading, refresh: run, setData }
}

/** Remembers a value in localStorage (used for the selected location/region). */
export function useStored<T extends string>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      return (window.localStorage.getItem(key) as T) || initial
    } catch {
      return initial
    }
  })
  const update = useCallback(
    (v: T) => {
      setValue(v)
      try {
        window.localStorage.setItem(key, v)
      } catch {
        /* private mode - not important */
      }
    },
    [key],
  )
  return [value, update]
}
