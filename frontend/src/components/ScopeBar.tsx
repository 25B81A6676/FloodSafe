import type { District, IndiaState, MonitoringLocation } from '../types'

/**
 * The geographic hierarchy: India → state → district → location.
 *
 * Presentational only. All selection state lives in App, because the map, the
 * dashboard and the command centre all read the same scope. Each level is
 * populated by the level above it, and every option here comes from the backend
 * — the frontend holds no hardcoded list of states, districts or places.
 */
export function ScopeBar({
  states,
  districts,
  locations,
  stateId,
  districtId,
  locationId,
  onState,
  onDistrict,
  onLocation,
  locationsLoading,
  locationNote,
}: {
  states: IndiaState[]
  districts: District[]
  locations: MonitoringLocation[]
  stateId: string
  districtId: string
  locationId: string
  onState: (id: string) => void
  onDistrict: (id: string) => void
  onLocation: (id: string) => void
  locationsLoading: boolean
  locationNote?: string | null
}) {
  const stateName = states.find((s) => s.id === stateId)?.name
  const districtName = districts.find((d) => d.id === districtId)?.name
  const locationName = locations.find((l) => l.id === locationId)?.name

  /* What one "location" means depends on how far the user has drilled in, so
     the label says so rather than leaving an ambiguous list. */
  const locationLabel = districtId ? 'Location' : stateId ? 'District centre' : 'State centre'

  return (
    <div className="scope-bar">
      <div className="scope-fields">
        <label className="field">
          <span className="field-label">Country</span>
          {/* One option today. Present as a select so the hierarchy reads
              consistently and a second country stays a data change. */}
          <select value="india" disabled aria-label="Country">
            <option value="india">India</option>
          </select>
        </label>

        <label className="field">
          <span className="field-label">State / UT</span>
          <select
            value={stateId}
            onChange={(e) => onState(e.target.value)}
            aria-label="State or union territory"
            disabled={states.length === 0}
          >
            <option value="">All India ({states.length})</option>
            {states.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="field-label">District</span>
          <select
            value={districtId}
            onChange={(e) => onDistrict(e.target.value)}
            aria-label="District"
            disabled={!stateId || districts.length === 0}
          >
            <option value="">
              {stateId ? `All districts (${districts.length})` : 'Select a state first'}
            </option>
            {districts.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>

        <label className="field field-location">
          <span className="field-label">{locationLabel}</span>
          <select
            value={locationId}
            onChange={(e) => onLocation(e.target.value)}
            aria-label={locationLabel}
            disabled={locationsLoading || locations.length === 0}
          >
            {locations.length === 0 && (
              <option value="">{locationsLoading ? 'Loading…' : 'None available'}</option>
            )}
            {locations.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
                {l.district && !districtId ? ` · ${l.district}` : ''}
              </option>
            ))}
          </select>
        </label>
      </div>

      <nav className="scope-trail" aria-label="Selected area">
        <span>India</span>
        {stateName && (
          <>
            <span className="sep" aria-hidden>
              ›
            </span>
            <span>{stateName}</span>
          </>
        )}
        {districtName && (
          <>
            <span className="sep" aria-hidden>
              ›
            </span>
            <span>{districtName}</span>
          </>
        )}
        {locationName && (
          <>
            <span className="sep" aria-hidden>
              ›
            </span>
            <strong>{locationName}</strong>
          </>
        )}
        {locationsLoading && <span className="scope-note">resolving…</span>}
        {locationNote && !locationsLoading && (
          <span className="scope-note" title={locationNote}>
            {locationNote}
          </span>
        )}
      </nav>
    </div>
  )
}
