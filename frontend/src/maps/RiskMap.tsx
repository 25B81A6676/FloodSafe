import 'leaflet/dist/leaflet.css'
import { useEffect, useMemo, useState } from 'react'
import {
  CircleMarker,
  MapContainer,
  Polyline,
  Popup,
  Rectangle,
  TileLayer,
  Tooltip as LTooltip,
  useMap,
} from 'react-leaflet'
import type {
  DashboardLocationRow,
  InfrastructureFeature,
  MapLayers,
  RiskClass,
  RiskMap as RiskMapData,
} from '../types'
import { RISK_COLOR, RISK_SYMBOL, fmt, fmtInt } from '../components/ui'

interface Props {
  riskMap: RiskMapData | null
  layers: MapLayers | null
  locations: DashboardLocationRow[]
  selectedId?: string | null
  onSelect?: (locationId: string) => void
  center: [number, number]
  zoom: number
  tall?: boolean
}

function Recentre({ center, zoom }: { center: [number, number]; zoom: number }) {
  const map = useMap()
  useEffect(() => {
    map.setView(center, zoom, { animate: true })
  }, [center[0], center[1], zoom]) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

/**
 * Scroll-wheel zoom is enabled only after the map is clicked, and disabled
 * again when the pointer leaves. Without this the map swallows the page scroll
 * and a user reading the dashboard gets trapped in it.
 */
function ScrollZoomGuard({ onArmedChange }: { onArmedChange: (armed: boolean) => void }) {
  const map = useMap()
  useEffect(() => {
    const arm = () => {
      map.scrollWheelZoom.enable()
      onArmedChange(true)
    }
    const disarm = () => {
      map.scrollWheelZoom.disable()
      onArmedChange(false)
    }
    map.on('click', arm)
    map.on('mouseout', disarm)
    return () => {
      map.off('click', arm)
      map.off('mouseout', disarm)
    }
  }, [map, onArmedChange])
  return null
}

const INFRA_STYLE: Record<string, { color: string; label: string; symbol: string }> = {
  hospital: { color: '#f87171', label: 'Hospital', symbol: 'H' },
  school: { color: '#fbbf24', label: 'School', symbol: 'S' },
  bridge: { color: '#e879f9', label: 'Bridge', symbol: 'B' },
  settlement: { color: '#94a3b8', label: 'Settlement', symbol: '•' },
}

export function RiskMap({
  riskMap,
  layers,
  locations,
  selectedId,
  onSelect,
  center,
  zoom,
  tall = false,
}: Props) {
  const [show, setShow] = useState({
    grid: true,
    rivers: true,
    streams: false,
    stations: true,
    infrastructure: false,
  })
  const [zoomArmed, setZoomArmed] = useState(false)

  const legend: RiskClass[] = useMemo(
    () =>
      riskMap?.legend ?? [
        { level: 'SAFE', min: 0, max: 20, color: '#16a34a', icon: '', label: 'Safe' },
        { level: 'LOW', min: 21, max: 40, color: '#eab308', icon: '', label: 'Low' },
        { level: 'MODERATE', min: 41, max: 60, color: '#f97316', icon: '', label: 'Moderate' },
        { level: 'HIGH', min: 61, max: 80, color: '#dc2626', icon: '', label: 'High' },
        { level: 'EXTREME', min: 81, max: 100, color: '#9333ea', icon: '', label: 'Extreme' },
      ],
    [riskMap],
  )

  const infra: InfrastructureFeature[] = useMemo(
    () => (layers?.infrastructure ?? []).filter((f) => f.kind !== 'settlement').slice(0, 400),
    [layers],
  )

  return (
    <div className={`map-shell ${tall ? 'tall' : ''}`}>
      <MapContainer
        center={center}
        zoom={zoom}
        scrollWheelZoom={false}
        style={{ height: '100%', width: '100%' }}
        preferCanvas
      >
        <Recentre center={center} zoom={zoom} />
        <ScrollZoomGuard onArmedChange={setZoomArmed} />
        {/* OpenStreetMap tiles, darkened in CSS to match the console theme.
            Attribution is preserved exactly as the licence requires. */}
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          maxZoom={18}
        />

        {/* Risk grid: discrete cells, each scored from its own real data. */}
        {show.grid &&
          riskMap?.cells.map((cell) => (
            <Rectangle
              key={cell.id}
              bounds={[
                [cell.bounds.min_lat, cell.bounds.min_lon],
                [cell.bounds.max_lat, cell.bounds.max_lon],
              ]}
              pathOptions={{
                color: cell.risk_color,
                weight: 1,
                opacity: 0.55,
                fillColor: cell.risk_color,
                fillOpacity: 0.13 + (cell.risk_score / 100) * 0.42,
              }}
            >
              <Popup>
                <div className="popup-title">
                  {RISK_SYMBOL[cell.risk_level]} {cell.risk_level} · {cell.risk_score}/100
                </div>
                <div className="popup-row">
                  <span>Cell</span>
                  <span>
                    r{cell.row} c{cell.col}
                  </span>
                </div>
                <div className="popup-row">
                  <span>Rainfall now</span>
                  <span>{fmt(cell.rainfall_mm_h, 1)} mm/h</span>
                </div>
                <div className="popup-row">
                  <span>Rain 24 h</span>
                  <span>{fmt(cell.rain_24h_mm, 1)} mm</span>
                </div>
                <div className="popup-row">
                  <span>Elevation</span>
                  <span>{fmtInt(cell.elevation_m)} m</span>
                </div>
                <div className="popup-row">
                  <span>Slope</span>
                  <span>{fmt(cell.slope_deg, 1)}°</span>
                </div>
                <div className="popup-row">
                  <span>Nearest river</span>
                  <span>{fmtInt(cell.river_distance_m)} m</span>
                </div>
                {cell.discharge_ratio != null && (
                  <div className="popup-row">
                    <span>Discharge</span>
                    <span>{fmt(cell.discharge_ratio, 2)}× mean</span>
                  </div>
                )}
                <div className="popup-note">
                  Top factor: {cell.top_factor ?? '—'}
                  <br />
                  {cell.features_available}/{cell.features_total} model features · {cell.freshness}
                </div>
              </Popup>
            </Rectangle>
          ))}

        {/* Real OpenStreetMap river geometry */}
        {show.rivers &&
          layers?.rivers.map((w) => (
            <Polyline
              key={`r${w.id}`}
              positions={w.coordinates}
              pathOptions={{ color: '#22d3ee', weight: 2.2, opacity: 0.8 }}
            >
              {w.name && <LTooltip sticky>{w.name}</LTooltip>}
            </Polyline>
          ))}

        {show.streams &&
          layers?.streams.map((w) => (
            <Polyline
              key={`s${w.id}`}
              positions={w.coordinates}
              pathOptions={{ color: '#0e7490', weight: 1, opacity: 0.55 }}
            >
              {w.name && <LTooltip sticky>{w.name}</LTooltip>}
            </Polyline>
          ))}

        {show.infrastructure &&
          infra.map((f) => {
            const style = INFRA_STYLE[f.kind] ?? INFRA_STYLE.settlement
            return (
              <CircleMarker
                key={f.id}
                center={[f.latitude, f.longitude]}
                radius={4}
                pathOptions={{
                  color: style.color,
                  fillColor: style.color,
                  fillOpacity: 0.85,
                  weight: 1,
                }}
              >
                <LTooltip>
                  {style.label}
                  {f.name ? `: ${f.name}` : ''}
                </LTooltip>
              </CircleMarker>
            )
          })}

        {/* Monitoring stations */}
        {show.stations &&
          locations.map((loc) => {
            const selected = loc.location_id === selectedId
            return (
              <CircleMarker
                key={loc.location_id}
                center={[loc.latitude, loc.longitude]}
                radius={selected ? 11 : 7}
                pathOptions={{
                  color: selected ? '#ffffff' : 'rgba(255,255,255,0.75)',
                  weight: selected ? 3 : 1.5,
                  fillColor: RISK_COLOR[loc.risk_level],
                  fillOpacity: 0.95,
                }}
                eventHandlers={{ click: () => onSelect?.(loc.location_id) }}
              >
                <LTooltip direction="top" offset={[0, -6]}>
                  <strong>{loc.name}</strong> · {loc.risk_level} {loc.risk_score}
                </LTooltip>
                <Popup>
                  <div className="popup-title">
                    {RISK_SYMBOL[loc.risk_level]} {loc.name}
                  </div>
                  <div className="popup-row">
                    <span>Risk</span>
                    <span style={{ color: RISK_COLOR[loc.risk_level], fontWeight: 700 }}>
                      {loc.risk_level} {loc.risk_score}/100
                    </span>
                  </div>
                  <div className="popup-row">
                    <span>District</span>
                    <span>{loc.district ?? '—'}</span>
                  </div>
                  <div className="popup-row">
                    <span>Rainfall now</span>
                    <span>{fmt(loc.rainfall_mm_h, 1)} mm/h</span>
                  </div>
                  <div className="popup-row">
                    <span>Rain 24 h</span>
                    <span>{fmt(loc.rain_24h_mm, 1)} mm</span>
                  </div>
                  <div className="popup-row">
                    <span>Elevation</span>
                    <span>{fmtInt(loc.elevation_m)} m</span>
                  </div>
                  <div className="popup-row">
                    <span>River status</span>
                    <span>{loc.river_status ?? '—'}</span>
                  </div>
                  <div className="popup-row">
                    <span>Nearest river</span>
                    <span>{fmtInt(loc.river_distance_m)} m</span>
                  </div>
                  <div className="popup-note">Top factor: {loc.top_factor ?? '—'}</div>
                  {onSelect && (
                    <button
                      className="btn btn-sm mt6"
                      style={{ width: '100%' }}
                      onClick={() => onSelect(loc.location_id)}
                    >
                      Open in dashboard
                    </button>
                  )}
                </Popup>
              </CircleMarker>
            )
          })}
      </MapContainer>

      <div className="map-overlay map-layers">
        <div className="legend-title">Layers</div>
        {(
          [
            ['grid', `Risk grid (${riskMap?.cells.length ?? 0})`],
            ['stations', `Stations (${locations.length})`],
            ['rivers', `Rivers (${layers?.rivers.length ?? 0})`],
            ['streams', `Streams (${layers?.stream_count_returned ?? 0})`],
            ['infrastructure', `Facilities (${infra.length})`],
          ] as const
        ).map(([key, label]) => (
          <label className="layer-toggle" key={key}>
            <input
              type="checkbox"
              checked={show[key]}
              onChange={(e) => setShow((s) => ({ ...s, [key]: e.target.checked }))}
            />
            {label}
          </label>
        ))}
      </div>

      <div className="map-overlay map-legend">
        <div className="legend-title">Flash-flood risk</div>
        {legend.map((c) => (
          <div className="legend-row" key={c.level}>
            <span className="legend-swatch" style={{ background: c.color }} aria-hidden>
              {RISK_SYMBOL[c.level]}
            </span>
            <span>{c.label}</span>
            <span className="legend-range">
              {c.min}–{c.max}
            </span>
          </div>
        ))}
        {show.rivers && (
          <div className="legend-row" style={{ marginTop: 6 }}>
            <span
              className="legend-swatch"
              style={{ background: 'none', border: 'none', borderTop: '2.5px solid #22d3ee', height: 0, borderRadius: 0 }}
              aria-hidden
            />
            <span>River (OSM)</span>
          </div>
        )}
      </div>

      {riskMap && (
        <div className="map-overlay map-stats">
          <div className="legend-title">Grid summary</div>
          <div className="mono">
            {riskMap.grid.rows}×{riskMap.grid.cols} cells · mean {fmt(riskMap.summary.mean, 1)} ·
            max {fmt(riskMap.summary.max, 0)}
          </div>
          <div className="tiny faint" style={{ marginTop: 3, maxWidth: 250 }}>
            Discrete per-cell assessments, not an interpolated surface.
          </div>
          <div className="tiny faint" style={{ marginTop: 4 }}>
            {zoomArmed ? 'Scroll to zoom · move away to release' : 'Click the map to enable scroll zoom'}
          </div>
        </div>
      )}
    </div>
  )
}
