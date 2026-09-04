import { useEffect, useState } from 'react'
import type { RiskLevel } from '../types'

/**
 * Resolves the design tokens out of CSS into computed values.
 *
 * Most of the app references tokens by name (`var(--color-accent)`), which is
 * how it should be. Two renderers cannot: Recharts writes colours onto SVG
 * presentation attributes, and Leaflet — running with `preferCanvas` — assigns
 * them to `ctx.strokeStyle`. Neither resolves `var()`; canvas silently ignores
 * the assignment and keeps whatever colour was set last, which is how a river
 * ends up painted the colour of a risk cell.
 *
 * This hook is the single sanctioned place that reads computed values.
 * `tokens.css` remains the source of truth and no component hardcodes a colour.
 */
export interface DesignTokens {
  // chart chrome
  axis: string
  grid: string
  marker: string
  tooltipBg: string
  tooltipBorder: string
  tooltipLabel: string
  // series
  primary: string
  projected: string
  discharge: string
  scenarioLabel: string
  // severity
  safe: string
  low: string
  moderate: string
  high: string
  extreme: string
  risk: Record<RiskLevel, string>
  // map layers
  river: string
  stream: string
  hospital: string
  school: string
  bridge: string
  settlement: string
  markerRing: string
  markerRingSelected: string
  // type
  fontXs: number
  fontSm: number
}

const FALLBACK: DesignTokens = {
  axis: '#787e86',
  grid: '#292e35',
  marker: '#9a9fa6',
  tooltipBg: '#14191f',
  tooltipBorder: '#61676e',
  tooltipLabel: '#9a9fa6',
  primary: '#4392f7',
  projected: '#a879f0',
  discharge: '#5cc4d8',
  scenarioLabel: '#cfd5dc',
  safe: '#39a055',
  low: '#e3b545',
  moderate: '#ef7b39',
  high: '#df473f',
  extreme: '#a255f4',
  risk: {
    SAFE: '#39a055',
    LOW: '#e3b545',
    MODERATE: '#ef7b39',
    HIGH: '#df473f',
    EXTREME: '#a255f4',
  },
  river: '#5cc4d8',
  stream: '#61676e',
  hospital: '#df473f',
  school: '#e3b545',
  bridge: '#a879f0',
  settlement: '#9a9fa6',
  markerRing: '#cfd5dc',
  markerRingSelected: '#e7ecf2',
  fontXs: 12,
  fontSm: 13,
}

function toPx(value: string, fallback: number): number {
  if (!value) return fallback
  const n = Number.parseFloat(value)
  if (Number.isNaN(n)) return fallback
  if (value.includes('rem')) {
    const root = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
    return n * root
  }
  return n
}

function readTokens(): DesignTokens {
  if (typeof window === 'undefined' || !document.documentElement) return FALLBACK
  const cs = getComputedStyle(document.documentElement)
  const c = (name: string, fb: string) => cs.getPropertyValue(name).trim() || fb
  const s = (name: string, fb: number) => toPx(cs.getPropertyValue(name).trim(), fb)

  const safe = c('--status-safe', FALLBACK.safe)
  const low = c('--status-low', FALLBACK.low)
  const moderate = c('--status-moderate', FALLBACK.moderate)
  const high = c('--status-high', FALLBACK.high)
  const extreme = c('--status-extreme', FALLBACK.extreme)

  return {
    axis: c('--color-neutral', FALLBACK.axis),
    grid: c('--color-rule', FALLBACK.grid),
    marker: c('--color-muted', FALLBACK.marker),
    tooltipBg: c('--color-paper-3', FALLBACK.tooltipBg),
    tooltipBorder: c('--color-rule-strong', FALLBACK.tooltipBorder),
    tooltipLabel: c('--color-muted', FALLBACK.tooltipLabel),
    primary: c('--color-accent', FALLBACK.primary),
    projected: c('--state-sim', FALLBACK.projected),
    discharge: c('--state-cached', FALLBACK.discharge),
    scenarioLabel: c('--color-ink-2', FALLBACK.scenarioLabel),
    safe,
    low,
    moderate,
    high,
    extreme,
    risk: { SAFE: safe, LOW: low, MODERATE: moderate, HIGH: high, EXTREME: extreme },
    river: c('--state-cached', FALLBACK.river),
    stream: c('--color-rule-strong', FALLBACK.stream),
    hospital: high,
    school: c('--state-stale', FALLBACK.school),
    bridge: c('--state-sim', FALLBACK.bridge),
    settlement: c('--color-muted', FALLBACK.settlement),
    markerRing: c('--color-ink-2', FALLBACK.markerRing),
    markerRingSelected: c('--color-ink', FALLBACK.markerRingSelected),
    fontXs: s('--text-xs', FALLBACK.fontXs),
    fontSm: s('--text-sm', FALLBACK.fontSm),
  }
}

export function useDesignTokens(): DesignTokens {
  const [tokens, setTokens] = useState<DesignTokens>(readTokens)

  useEffect(() => {
    let cancelled = false
    // Webfonts resolve asynchronously; re-read once they settle so chart type
    // metrics match the rest of the page.
    document.fonts?.ready
      ?.then(() => {
        if (!cancelled) setTokens(readTokens())
      })
      .catch(() => {
        /* font loading is best-effort — the first read already works */
      })
    return () => {
      cancelled = true
    }
  }, [])

  return tokens
}
