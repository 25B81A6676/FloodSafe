export type RiskLevel = 'SAFE' | 'LOW' | 'MODERATE' | 'HIGH' | 'EXTREME'
export type Confidence = 'HIGH' | 'MEDIUM' | 'LOW'
export type Freshness = 'LIVE' | 'CACHED' | 'STALE_CACHE' | 'DEMO' | 'SIMULATION'
export type RunMode = 'LIVE' | 'SIMULATION'
export type Severity = 'INFO' | 'ADVISORY' | 'WARNING' | 'CRITICAL'

export interface LatLon {
  latitude: number
  longitude: number
}

export interface BBox {
  min_lat: number
  min_lon: number
  max_lat: number
  max_lon: number
}

export interface Region {
  id: string
  name: string
  display_name: string
  country: string | null
  terrain_type: string | null
  description: string | null
  bbox: BBox
  center: LatLon
  default_zoom: number
  timezone: string
  location_count: number
  rivers: { name: string; type: string; note?: string }[]
  elevation_range_m: { min: number; max: number } | null
  is_default: boolean
}

export interface MonitoringLocation {
  id: string
  region_id: string
  name: string
  district: string | null
  latitude: number
  longitude: number
  reference_elevation_m: number | null
  nearest_river: string | null
  settlement_type: string | null
  exposure: string | null
}

export interface Contributor {
  key: string
  factor: string
  group: string
  impact: 'HIGH' | 'MEDIUM' | 'LOW'
  icon: string
  value: number | null
  unit: string
  display_value: string
  normalized: number | null
  weight: number | null
  contribution: number
  share_pct: number
  source: string | null
  freshness: Freshness | null
  simulated: boolean
  detail: string | null
  rationale: string | null
}

export interface RiskAssessment {
  location_id: string
  risk_score: number
  risk_score_precise: number
  risk_level: RiskLevel
  risk_label: string
  risk_color: string
  confidence: Confidence
  data_quality_score: number
  data_quality_notes: string[]
  timestamp: string
  mode: RunMode
  scenario_id: string | null
  trend: 'INCREASING' | 'DECREASING' | 'STEADY' | 'UNKNOWN'
  score_delta: number | null
  contributors: Contributor[]
  primary_factors: Contributor[]
  model: {
    id: string
    name: string
    version: string
    kind: string
    weight_coverage: number
    notes: string[]
  }
  feature_summary: {
    available: number
    total: number
    missing: string[]
    simulated: string[]
    freshness: Record<string, number>
  }
  context: RiskContext
  warnings: string[]
  disclaimer: string
}

export interface RiskContext {
  rainfall: {
    current_mm_h: number | null
    rain_1h: number | null
    rain_3h: number | null
    rain_6h: number | null
    rain_24h: number | null
    forecast_24h: number | null
    forecast_peak_mm_h: number | null
    trend: string | null
  }
  terrain: {
    elevation_m: number | null
    slope_deg: number | null
    relief_m: number | null
    terrain_class: string | null
    elevation_band: string | null
    aspect: string | null
  }
  hydrology: {
    river_status: string | null
    river_trend: string | null
    discharge_m3s: number | null
    mean_30d_m3s: number | null
    discharge_ratio: number | null
    nearest_waterway: string | null
    nearest_waterway_type: string | null
    river_distance_m: number | null
    stream_density: number | null
  }
  soil: {
    api_mm: number | null
    saturation_class: string | null
    total_14d_mm: number | null
  }
  climatology: {
    available: boolean
    percentiles: Record<string, number> | null
    sample_days: number | null
    max_observed_mm: number | null
  }
}

export interface Alert {
  id: string
  location_id: string
  location_name: string
  risk_level: RiskLevel
  risk_score: number
  severity: Severity
  headline: string
  message: string
  actions: string[]
  drivers: string[]
  confidence: Confidence
  issued_at: string
  mode: RunMode
  is_simulation: boolean
  simulation_notice: string | null
  advisory_footer: string
}

export interface SourceFreshness {
  label: string
  source: string | null
  source_key: string | null
  freshness: Freshness
  age_minutes: number | null
  attribution: string | null
  notes: string[]
}

export interface WeatherSeriesPoint {
  time: string
  precipitation: number | null
  temperature?: number | null
  probability?: number | null
}

export interface Weather {
  location_id: string
  source: string
  source_key: string
  attribution: string
  freshness: Freshness
  age_minutes: number
  observed_at: string
  timezone: string | null
  model_elevation_m: number | null
  current: {
    precipitation_mm_h: number | null
    temperature_c: number | null
    apparent_temperature_c: number | null
    humidity_pct: number | null
    wind_kmh: number | null
    wind_direction_deg: number | null
    pressure_hpa: number | null
    cloud_cover_pct: number | null
    weather_code: number | null
    observed_at: string | null
  }
  accumulation: Record<string, number | null>
  forecast: {
    rain_6h: number | null
    rain_12h: number | null
    rain_24h: number | null
    max_hourly_next_24h: number | null
    peak_time: string | null
    max_probability_next_24h: number | null
  }
  trend: { slope_mm_per_h2: number; direction: string }
  series: { past: WeatherSeriesPoint[]; forecast: WeatherSeriesPoint[] }
  notes: string[]
}

export interface Terrain {
  elevation_m: number | null
  slope_deg: number | null
  aspect_deg: number | null
  aspect_cardinal: string | null
  relief_m: number | null
  terrain_class: string
  elevation_band: string
  source: string
  freshness: Freshness
  method: string
  notes: string[]
}

export interface Hydrology {
  discharge_m3s: number | null
  mean_30d_m3s: number | null
  max_30d_m3s: number | null
  discharge_ratio: number | null
  forecast_peak_7d_m3s: number | null
  forecast_change_pct: number | null
  river_status: string
  river_trend: string
  source: string
  freshness: Freshness
  age_minutes: number
  observed_at: string
  valid_date: string | null
  representative_cell: {
    latitude: number | null
    longitude: number | null
    distance_m: number | null
    used_neighbour: boolean
    centre_cell_mean_m3s: number | null
  }
  series: { date: string; discharge: number | null; kind: string }[]
  method: string
  notes: string[]
}

export interface RiverContext {
  river_distance_m: number | null
  nearest_waterway_name: string | null
  nearest_waterway_type: string | null
  stream_density_km_per_km2: number | null
  rivers_within_radius: number
  streams_within_radius: number
  search_radius_m: number
  freshness: Freshness
  source: string
  method: string
}

export interface TimelinePoint {
  time: string
  risk_score: number
  risk_level: RiskLevel
  precipitation: number
  probability?: number | null
  kind: 'modelled' | 'projected'
}

export interface MonitoringSnapshot {
  location: MonitoringLocation
  region_id: string
  risk: RiskAssessment
  alert: Alert
  weather: Weather
  terrain: Terrain
  hydrology: Hydrology
  river_context: RiverContext
  climatology: Record<string, unknown>
  antecedent: {
    api_mm: number | null
    saturation_class: string | null
    total_14d_mm: number | null
    wet_days_14d: number | null
    recent_daily: { date: string; precipitation_mm: number }[]
    method: string
    freshness: Freshness
  }
  timeline: { past: TimelinePoint[]; forecast: TimelinePoint[]; method: string }
  stored_history: { risk_score: number; risk_level: string; computed_at: string }[]
  simulation: {
    active: boolean
    scenario_id: string | null
    scenario_name: string | null
    overrides: Record<string, number>
    readouts: SimulationReadouts | null
  }
  data_freshness: {
    sources: SourceFreshness[]
    live: number
    cached: number
    degraded: number
    total: number
    overall: string
  }
  mode: RunMode
  generated_at: string
}

export interface SimulationReadouts {
  water_rise_rate_cm_per_hr: number
  river_level_label: string
  is_scenario_attribute: boolean
  note: string
}

export interface Scenario {
  id: string
  name: string
  short_name: string
  description: string
  narrative: string
  severity: string
  order: number
  expected_risk_level: string
  expected_score_range: [number, number]
  hydrology_label: string
  water_rise_rate_cm_per_hr: number
  overrides: Record<string, number>
  data_kind: string
}

export interface SimulationControl {
  key: string
  label: string
  group: string
  unit: string
  min: number
  max: number
  step: number
  help: string
  model_weight: number | null
  model_label: string | null
  rationale: string | null
  direction: string
}

export interface RiskCell {
  id: string
  row: number
  col: number
  center: LatLon
  bounds: BBox
  risk_score: number
  risk_level: RiskLevel
  risk_color: string
  confidence: Confidence
  top_factor: string | null
  top_factor_value: string | null
  rainfall_mm_h: number | null
  rain_24h_mm: number | null
  forecast_24h_mm: number | null
  elevation_m: number | null
  slope_deg: number | null
  river_distance_m: number | null
  nearest_waterway: string | null
  discharge_ratio: number | null
  discharge_m3s: number | null
  freshness: Freshness
  features_available: number
  features_total: number
}

export interface RiskMap {
  region_id: string
  region_name: string
  generated_at: string
  mode: RunMode
  scenario_id: string | null
  grid: { rows: number; cols: number; bbox: BBox; cell_count: number }
  cells: RiskCell[]
  summary: {
    count: number
    mean: number | null
    max: number | null
    min: number | null
    distribution: Record<RiskLevel, number>
    highest_cell: RiskCell | null
  }
  legend: RiskClass[]
  method: string
  attribution: string[]
}

export interface RiskClass {
  level: RiskLevel
  min: number
  max: number
  color: string
  icon: string
  label: string
}

export interface Waterway {
  id: number
  name: string | null
  waterway: string
  coordinates: [number, number][]
}

export interface InfrastructureFeature {
  id: string
  kind: 'hospital' | 'school' | 'settlement' | 'bridge'
  name: string | null
  subtype: string | null
  latitude: number
  longitude: number
}

export interface MapLayers {
  region_id: string
  bbox: BBox
  rivers: Waterway[]
  streams: Waterway[]
  named_rivers: string[]
  waterway_freshness: Freshness
  waterway_age_minutes: number | null
  waterway_notes: string[]
  stream_count_total: number
  stream_count_returned: number
  /** True when only part of the stream geometry was sent to the browser. */
  stream_geometry_truncated?: boolean
  infrastructure: InfrastructureFeature[]
  infrastructure_counts: Record<string, number>
  infrastructure_freshness: Freshness
  attribution: string
  generated_at: string
}

export interface DashboardLocationRow {
  location_id: string
  name: string
  district: string | null
  latitude: number
  longitude: number
  settlement_type: string | null
  nearest_river: string | null
  risk_score: number
  risk_level: RiskLevel
  risk_color: string
  confidence: Confidence
  trend: string
  score_delta: number | null
  top_factor: string | null
  rainfall_mm_h: number | null
  rain_24h_mm: number | null
  forecast_24h_mm: number | null
  elevation_m: number | null
  slope_deg: number | null
  river_status: string | null
  discharge_ratio: number | null
  river_distance_m: number | null
  saturation: string | null
  alert_severity: Severity
  mode: RunMode
}

export interface DashboardSummary {
  region: {
    id: string
    name: string
    center: LatLon
    bbox: BBox
    default_zoom: number
    terrain_type: string | null
    scope: Scope
    state_id: string | null
    state_name: string | null
  }
  /** What one ranked row is at this scope: a state, a district or a location. */
  row_kind: 'state' | 'district' | 'location'
  generated_at: string
  mode: RunMode
  scenario_id: string | null
  scenario_name: string | null
  totals: {
    monitoring_locations: number
    distribution: Record<RiskLevel, number>
    at_or_above_high: number
  }
  risk_summary: { count: number; mean: number | null; max: number | null; min: number | null }
  highest_risk: DashboardLocationRow | null
  rainfall_summary: {
    max_intensity_mm_h: number | null
    mean_intensity_mm_h: number | null
    max_24h_mm: number | null
    mean_24h_mm: number | null
    max_forecast_24h_mm: number | null
    wettest_location: { name: string; rain_24h_mm: number } | null
  }
  locations: DashboardLocationRow[]
  alerts: {
    total: number
    actionable: number
    by_severity: Record<string, number>
    highest_severity: Severity
    top_alert: Alert | null
    alerts: Alert[]
    actionable_alerts: Alert[]
    advisory_footer: string
  }
  legend: RiskClass[]
  data_freshness: {
    feature_states: Record<string, number>
    sources: SourceHealth[]
  }
  simulation: { active: boolean; scenario_id: string | null; overrides: Record<string, number> }
  attribution: string[]
  infrastructure?: {
    counts: Record<string, number>
    total: number
    freshness: Freshness
    features: InfrastructureFeature[]
  }
  exposure?: {
    radius_km: number
    high_risk_locations: number
    counts: Record<string, number>
    total: number
    features: (InfrastructureFeature & { near_location: string; distance_m: number })[]
    note: string
  }
}

export interface SourceHealth {
  name: string
  label?: string
  status: 'OK' | 'DEGRADED' | 'DOWN' | 'UNKNOWN'
  last_success: string | null
  last_failure: string | null
  last_error: string | null
  consecutive_failures: number
  total_calls: number
  total_failures: number
  last_latency_ms: number | null
}

export interface SystemSources {
  overall: string
  network_enabled: boolean
  sources: SourceHealth[]
  cache: {
    entries: number
    unexpired_entries: number
    total_hits: number
    by_source: { source: string; entries: number; hits: number; newest: string }[]
  }
  timestamp: string
}

export interface ModelConfig {
  model_id: string
  model_name: string
  version: string
  description: string
  methodology_note: string
  feature_count: number
  weight_sum: number
  features: {
    key: string
    label: string
    group: string
    unit: string
    weight: number
    direction: string
    curve: [number, number][]
    rationale: string
  }[]
  risk_classes: RiskClass[]
  available_models: {
    id: string
    name: string
    version: string
    kind: string
    available: boolean
    active: boolean
    description: string
  }[]
  pipeline: string[]
}

/* ---------------------------------------------------------------------------
 * India administrative hierarchy: country -> state -> district -> location.
 * Boundaries come from OpenStreetMap via scripts/build_india_geo.py; the
 * frontend never derives or invents a coordinate of its own.
 * ------------------------------------------------------------------------ */
export interface IndiaState {
  id: string
  name: string
  iso_code: string
  type: 'state' | 'union_territory'
  bbox: BBox
  center: LatLon
  district_count: number
  osm_relation_id: number
}

export interface District {
  id: string
  name: string
  state_id: string
  state_name: string
  bbox: BBox
  center: LatLon
  osm_relation_id: number
}

export interface GeographyInfo {
  country: { id: string; name: string }
  bbox: BBox
  levels: string[]
  state_count: number
  district_count: number
  available: boolean
  generated_at: string | null
  source: string | null
  attribution: string | null
}

/** Every geographic level the app can be scoped to. */
export type Scope = 'national' | 'state' | 'district' | 'region'

export interface LocationList {
  region_id: string
  region_name: string
  scope: Scope
  count: number
  locations: MonitoringLocation[]
  freshness: Freshness | 'STATIC'
  notes: string[]
  age_minutes?: number | null
  attribution?: string
  total_found?: number
  truncated?: boolean
}

/* ---------------------------------------------------------------------------
 * Flood-alert push notifications.
 * A registration token never crosses this boundary — only a masked hint.
 * ------------------------------------------------------------------------ */
export interface FirebaseConfig {
  apiKey: string | null
  authDomain: string | null
  projectId: string | null
  messagingSenderId: string | null
  appId: string | null
}

export interface NotificationConfig {
  configured: boolean
  firebase: FirebaseConfig
  vapidKey: string | null
  serverReady: boolean
}

export interface AlertDevice {
  id: number
  label: string | null
  /** Masked fragment of the FCM token, enough to tell two phones apart. */
  token_hint: string
  location_id: string | null
  location_name: string | null
  district: string | null
  state_id: string | null
  state_name: string | null
  latitude: number | null
  longitude: number | null
  active: boolean
  created_at: string
  last_seen_at: string
}

export interface AlertDispatch {
  id: number
  location_id: string
  location_name: string | null
  risk_level: string
  risk_score: number | null
  previous_level: string | null
  mode: string
  kind: string
  targeted: number
  accepted: number
  rejected: number
  status: string
  detail: string | null
  sent_at: string
  episode_id?: string | null
}

export interface NotificationStatus {
  configured: boolean
  project_id: string | null
  service_account: string | null
  credential_source: string | null
  test_mode: boolean
  simulation_alerts_enabled?: boolean
  cooldown_minutes: number
  targeting_scope: string
  trigger_levels: string[]
  active_devices: number
  total_devices: number
  devices: AlertDevice[]
  recent_dispatches: AlertDispatch[]
  note: string
}

/** Outcome of a simulator demonstration push, returned by /simulation/run.
 *  `accepted` is Firebase accepting the request, never proof of delivery. */
export interface DemoAlert {
  id?: number
  kind?: 'SIMULATION'
  episode_id?: string
  location_id?: string
  location_name?: string
  risk_level?: string
  risk_score?: number | null
  targeted?: number
  accepted?: number
  rejected?: number
  invalid_tokens?: number
  status: string
  detail?: string | null
}
