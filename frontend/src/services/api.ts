/**
 * The only place the frontend talks to a server.
 *
 * All environmental data reaches the browser through the FastAPI backend -
 * no component calls Open-Meteo, Overpass or any other external service
 * directly, and no risk calculation happens in the browser.
 */
import type {
  AlertDevice,
  DashboardSummary,
  DemoAlert,
  District,
  GeographyInfo,
  IndiaState,
  LocationList,
  MapLayers,
  ModelConfig,
  MonitoringSnapshot,
  NotificationConfig,
  NotificationStatus,
  Region,
  RiskMap,
  Scenario,
  SimulationControl,
  SimulationReadouts,
  SystemSources,
} from '../types'

const BASE = '/api'

/* Cache-busting token for simulation.

   Read responses are cached by the CDN, which is what makes the app fast. But
   a CDN hit never reaches the server, so the server cannot decide to skip the
   cache while the simulator is running - by then it is not being asked. The
   dashboard would keep showing the pre-flood picture during the one minute
   that matters.

   So the client changes the URL instead. While a simulation is active every
   read carries its episode id, which is a URL the CDN has never seen and
   therefore cannot answer from its copy; the server then sets no-store on it.
   Leaving the simulation clears the token and the ordinary, cached URLs come
   back. */
let cacheBust: string | null = null

export function setCacheBust(token: string | null): void {
  cacheBust = token
}

function withCacheBust(path: string): string {
  if (!cacheBust) return path
  return `${path}${path.includes('?') ? '&' : '?'}_sim=${encodeURIComponent(cacheBust)}`
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly path: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    const url = !init?.method || init.method === 'GET' ? withCacheBust(path) : path
    response = await fetch(`${BASE}${url}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch (cause) {
    throw new ApiError(
      'Cannot reach the FloodSafe backend. Is it running on port 8000?',
      0,
      path,
    )
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body?.detail) detail = String(body.detail)
      else if (body?.error) detail = String(body.error)
    } catch {
      /* response had no JSON body */
    }
    throw new ApiError(detail, response.status, path)
  }
  return (await response.json()) as T
}

const qs = (params: Record<string, string | number | boolean | undefined>) => {
  const search = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null) search.set(k, String(v))
  }
  const s = search.toString()
  return s ? `?${s}` : ''
}

export const api = {
  health: () => request<{ status: string; version: string }>('/health'),

  sources: () => request<SystemSources>('/system/sources'),

  regions: () =>
    request<{ default_region_id: string; count: number; regions: Region[] }>('/regions'),

  /** The India → state → district hierarchy. Boundaries, never risk values. */
  geography: () => request<GeographyInfo>('/geography'),

  states: () =>
    request<{ country: string; count: number; states: IndiaState[]; attribution: string | null }>(
      '/geography/states',
    ),

  districts: (stateId: string) =>
    request<{
      state_id: string
      state_name: string
      count: number
      districts: District[]
      attribution: string | null
    }>(`/geography/states/${encodeURIComponent(stateId)}/districts`),

  /** Locations for any scope: a curated region, 'india', a state or a district. */
  locations: (regionId?: string) =>
    request<LocationList>(`/locations${qs({ region_id: regionId })}`),

  monitoring: (locationId: string, opts: { refresh?: boolean; timeline?: boolean } = {}) =>
    request<MonitoringSnapshot>(
      `/monitoring/${locationId}${qs({ refresh: opts.refresh, timeline: opts.timeline })}`,
    ),

  dashboard: (regionId?: string, refresh = false) =>
    request<DashboardSummary>(`/dashboard/summary${qs({ region_id: regionId, refresh })}`),

  authority: (regionId?: string, refresh = false) =>
    request<DashboardSummary>(`/dashboard/authority${qs({ region_id: regionId, refresh })}`),

  riskMap: (regionId?: string, refresh = false) =>
    request<RiskMap>(`/risk/map${qs({ region_id: regionId, refresh })}`),

  mapLayers: (regionId?: string) =>
    request<MapLayers>(`/gis/layers${qs({ region_id: regionId })}`),

  modelConfig: () => request<ModelConfig>('/risk/model'),

  scenarios: () =>
    request<{ count: number; scenarios: Scenario[]; notice: string }>('/simulation/scenarios'),

  simulationControls: () =>
    request<{ controls: SimulationControl[]; note: string }>('/simulation/controls'),

  simulationState: () =>
    request<{
      active: boolean
      scenario_id: string | null
      overrides: Record<string, number>
      readouts: SimulationReadouts | null
    }>('/simulation/state'),

  runSimulation: (body: {
    scenario_id?: string | null
    overrides?: Record<string, number>
    merge?: boolean
    location_id?: string
  }) =>
    request<{
      active: boolean
      scenario_id: string | null
      overrides: Record<string, number>
      rejected: string[]
      readouts: SimulationReadouts
      monitoring?: MonitoringSnapshot
      episode_id?: string | null
      /** Present when a location was simulated; null if no alert was due. */
      demo_alert?: DemoAlert | null
    }>('/simulation/run', { method: 'POST', body: JSON.stringify(body) }),

  /* ---- flood-alert notifications ---- */
  notificationConfig: () => request<NotificationConfig>('/notifications/config'),

  notificationStatus: () => request<NotificationStatus>('/notifications/status'),

  registerDevice: (body: {
    fcm_token: string
    location_id?: string
    location_name?: string
    district?: string | null
    state_id?: string | null
    state_name?: string | null
    latitude?: number
    longitude?: number
    label?: string
  }) =>
    request<{
      registered: boolean
      device: AlertDevice
      active_devices: number
      notifications_configured: boolean
      notice: string | null
    }>('/notifications/register', { method: 'POST', body: JSON.stringify(body) }),

  sendTestAlert: (locationId?: string) =>
    request<{
      status: string
      targeted: number
      accepted: number
      rejected: number
      detail: string | null
      devices: string[]
    }>(`/notifications/test${qs({ location_id: locationId })}`, { method: 'POST' }),

  setDeviceActive: (deviceId: number, active: boolean) =>
    request<{ device: AlertDevice }>(
      `/notifications/devices/${deviceId}/${active ? 'enable' : 'disable'}`,
      { method: 'POST' },
    ),

  resetSimulation: (locationId?: string) =>
    request<{ active: boolean; monitoring?: MonitoringSnapshot }>(
      `/simulation/reset${qs({ location_id: locationId })}`,
      { method: 'POST' },
    ),
}
