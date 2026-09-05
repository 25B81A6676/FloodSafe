import { Card, ErrorBox, Skeleton, fmt } from '../components/ui'
import { useAsync } from '../hooks/useApi'
import { api } from '../services/api'

const SOURCES = [
  {
    name: 'Open-Meteo Forecast API',
    role: 'Weather',
    gives: 'Current and hourly precipitation, temperature, humidity, wind, pressure, and a 48-hour forecast.',
    licence: 'Free, no API key. Data CC BY 4.0.',
    url: 'https://open-meteo.com/',
  },
  {
    name: 'Copernicus GloFAS (via Open-Meteo Flood API)',
    role: 'Hydrology',
    gives: 'Modelled daily river discharge with a 7-day forecast. An independent signal that does not derive from the rainfall feed.',
    licence: 'Copernicus Emergency Management Service, free and open.',
    url: 'https://global-flood.emergency.copernicus.eu/',
  },
  {
    name: 'Copernicus DEM GLO-90 (via Open-Meteo Elevation API)',
    role: 'Terrain',
    gives: 'Ground elevation. Slope, aspect and local relief are derived here from a 3×3 sampling stencil.',
    licence: 'Copernicus, free and open.',
    url: 'https://open-meteo.com/en/docs/elevation-api',
  },
  {
    name: 'ECMWF ERA5 (via Open-Meteo Archive API)',
    role: 'Climatology',
    gives: 'Multi-year daily rainfall history, used to express today’s rainfall as a percentile of the same location’s own record.',
    licence: 'Copernicus / ECMWF, free and open.',
    url: 'https://open-meteo.com/en/docs/historical-weather-api',
  },
  {
    name: 'OpenStreetMap via Overpass API',
    role: 'GIS',
    gives: 'River and stream geometry for true point-to-polyline distance, drainage density, and mapped hospitals, schools and bridges.',
    licence: 'Map data © OpenStreetMap contributors, ODbL.',
    url: 'https://www.openstreetmap.org/copyright',
  },
  {
    name: 'OpenTopoData (SRTM) and Open-Elevation',
    role: 'Terrain fallback',
    gives: 'Secondary elevation providers used automatically if the primary DEM service fails.',
    licence: 'Free, no API key.',
    url: 'https://www.opentopodata.org/',
  },
]

export function Methodology() {
  const config = useAsync(() => api.modelConfig(), [])
  const cfg = config.data

  return (
    <div className="grid" style={{ gap: 'var(--space-md)', gridTemplateColumns: 'minmax(0, 1fr)' }}>
      <Card title="How the risk score is produced" icon="🔬">
        <div className="prose">
          <p>
            FloodSafe performs <strong>India-wide multi-source flash-flood risk assessment</strong>.
            It estimates how <em>susceptible</em> a location currently is to flash flooding by fusing
            five independent open data sources into a single transparent score. It is a
            decision-support prototype, not a validated hydrological forecast, and it does not
            perform flood prediction in the meteorological sense.
          </p>
          <p>
            The 0–100 value is a <strong>risk score, not a calibrated probability of flooding</strong>.
            A score of 60 does not mean a 60&nbsp;% chance of a flood; it means this location scores
            higher on the weighted combination of the twelve indicators below than a location
            scoring 40.
          </p>

          <h3>Geographic coverage</h3>
          <p>
            The platform covers all of India — {' '}
            <strong>28 states and 8 union territories</strong>, and every district within them.
            Administrative boundaries come from OpenStreetMap relations (states are{' '}
            <code>admin_level=4</code>, districts <code>admin_level=5</code>) and are refreshed by a
            generation script rather than typed in by hand, so no coordinate in the hierarchy is
            invented.
          </p>
          <p>
            Coverage of the <em>hierarchy</em> is not the same as coverage of live{' '}
            <em>measurements</em>, and the two are never conflated in this interface. Every location
            in India can be scored, but each feature independently reports whether its value came
            from a live call, a cache, a stale cache, or was unavailable. Where a source cannot
            answer, the feature is dropped and the remaining weights are renormalised — no value is
            substituted.
          </p>
          <p>
            Scope is chosen with the India → state → district → location selector. The grid becomes
            finer as the scope narrows, because every grid cell costs real upstream API calls: a
            national view is deliberately coarse and each cell is an independent assessment at its
            own centre point, not a claim about every square kilometre it covers.
          </p>
        </div>

        <div className="pipeline">
          {(cfg?.pipeline ?? [
            'Multi-source acquisition',
            'Validation and normalisation',
            'Feature engineering',
            'Weighted risk model',
            'Risk score and classification',
            'Spatial visualisation and alerting',
          ]).map((step, i) => (
            <div className="pipeline-step" key={step}>
              <span className="pipeline-num">{i + 1}</span>
              <span>{step}</span>
            </div>
          ))}
        </div>

        <div className="prose mt14">
          <h3>Why a weighted model rather than machine learning</h3>
          <p>
            A supervised model needs labelled flood events. There is no free, automatically
            obtainable, labelled flash-flood event dataset for the pilot region, so this prototype
            ships a transparent weighted model and reports <strong>no accuracy figures at all</strong>.
            Inventing a validation score would be worse than having none.
          </p>
          <p>
            The machine-learning pipeline is nonetheless built and wired in:{' '}
            <code>ml/features.py</code>, <code>ml/train.py</code> and <code>ml/evaluate.py</code>{' '}
            exist, and <code>SklearnRiskModel</code> will load and take over automatically from{' '}
            <code>ml/models/active_model.joblib</code> the moment a legitimately trained model is
            placed there. Swapping the model requires no change to the API, map or dashboard.
          </p>

          <h3>Handling missing data</h3>
          <p>
            If a source fails, its features are excluded and the remaining weights are{' '}
            <strong>renormalised</strong> rather than treated as zero. Substituting zero would
            silently lower the risk score during an outage — precisely the wrong behaviour for a
            warning system. Model coverage and data confidence are both surfaced in the UI.
          </p>

          <h3>Region-aware normalisation</h3>
          <p>
            The same twelve features and the same weighted-sum arithmetic are used for every
            location in India — there is no per-state formula. What the architecture{' '}
            <em>does</em> allow is per-region normalisation curves, because the same rainfall total
            does not carry the same meaning in Rajasthan and Assam. Rainfall breakpoints already
            follow the India Meteorological Department rainfall classes, and the{' '}
            <code>rainfall_anomaly</code> feature is expressed as a percentile of each location&apos;s
            own ERA5 history, which is the mechanism that makes a single global configuration
            locally meaningful today.
          </p>
          <p>
            Terrain and hydrology breakpoints are <strong>engineering assumptions</strong>, labelled
            as such in <code>risk_weights.json</code> and reproduced in the table below. They were
            chosen to spread observed values across the 0–1 range, not derived from Indian flood
            records. Adding basin-specific or state-specific curves is a configuration change, not a
            code change, and none have been added on the basis of guesswork.
          </p>

          <h3>What this prototype cannot do</h3>
          <ul>
            <li>It does not simulate hydraulics: no inundation depth, extent or arrival time.</li>
            <li>It is not calibrated against observed flood events, so the score is relative, not probabilistic.</li>
            <li>It does not claim India-wide real-time coverage: live data depends on what each upstream source can answer for each point, and that is reported per feature.</li>
            <li>It has no access to India Meteorological Department or Central Water Commission internal feeds. No official Indian government API is consumed anywhere in this build.</li>
            <li>It does not compute evacuation routes.</li>
            <li>GloFAS resolves rivers on a ~5 km grid, too coarse for small headwater catchments.</li>
            <li>OpenStreetMap coverage of small streams and settlements varies by district, which affects drainage density and the location list.</li>
            <li>It must never be the sole basis for an evacuation decision.</li>
          </ul>
        </div>
      </Card>

      <Card title="Model features and weights" icon="⚖">
        {config.error && <ErrorBox error={config.error} onRetry={config.refresh} />}
        {!cfg && !config.error && <Skeleton height={280} />}
        {cfg && (
          <>
            <div className="row wrap" style={{ gap: 'var(--space-xs)', marginBottom: 'var(--space-sm)' }}>
              <span className="badge badge-neutral">{cfg.model_name}</span>
              <span className="badge badge-neutral">v{cfg.version}</span>
              <span className="badge badge-neutral">{cfg.feature_count} features</span>
              <span className="badge badge-neutral">weights sum {fmt(cfg.weight_sum, 2)}</span>
            </div>
            <p className="small muted">{cfg.methodology_note}</p>

            <div className="table-wrap" style={{ maxHeight: 'none' }}>
              <table className="feature-table">
                <thead>
                  <tr>
                    <th>Feature</th>
                    <th>Group</th>
                    <th className="num">Weight</th>
                    <th>Unit</th>
                    <th>Direction</th>
                    <th>Why it matters</th>
                  </tr>
                </thead>
                <tbody>
                  {cfg.features
                    .slice()
                    .sort((a, b) => b.weight - a.weight)
                    .map((f) => (
                      <tr key={f.key} style={{ cursor: 'default' }}>
                        <td>
                          <strong>{f.label}</strong>
                        </td>
                        <td className="muted">{f.group}</td>
                        <td className="num">
                          <span
                            style={{
                              display: 'inline-block',
                              width: '2.75rem',
                              height: 5,
                              borderRadius: 'var(--radius-sm)',
                              background: 'var(--color-paper-4)',
                              position: 'relative',
                              marginRight: 'var(--space-xs)',
                              verticalAlign: 'middle',
                            }}
                          >
                            <span
                              style={{
                                position: 'absolute',
                                inset: 0,
                                width: `${(f.weight / 0.16) * 100}%`,
                                background: 'var(--color-accent)',
                                borderRadius: 'var(--radius-sm)',
                              }}
                            />
                          </span>
                          {f.weight.toFixed(2)}
                        </td>
                        <td className="muted">{f.unit}</td>
                        <td className="muted">{f.direction}</td>
                        <td className="rationale">{f.rationale}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>

            <div className="mt14">
              <h4 style={{ fontSize: 'var(--text-sm)', textTransform: 'uppercase', letterSpacing: '0.6px', color: 'var(--color-muted)' }}>
                Risk classes
              </h4>
              <div className="row wrap mt6" style={{ gap: 'var(--space-xs)' }}>
                {cfg.risk_classes.map((c) => (
                  <span
                    key={c.level}
                    className="risk-pill"
                    style={{ background: c.color, minWidth: '8.125rem' }}
                  >
                    {c.label}
                    <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)' }}>
                      {c.min}–{c.max}
                    </span>
                  </span>
                ))}
              </div>
            </div>

            <div className="mt14">
              <h4 style={{ fontSize: 'var(--text-sm)', textTransform: 'uppercase', letterSpacing: '0.6px', color: 'var(--color-muted)' }}>
                Model registry
              </h4>
              {cfg.available_models.map((m) => (
                <div className="source-row" key={m.id}>
                  <div className="source-name">
                    <b>
                      {m.name}{' '}
                      {m.active && <span className="chip" style={{ marginLeft: 'var(--space-xs)' }}>active</span>}
                    </b>
                    <small style={{ whiteSpace: 'normal' }}>{m.description}</small>
                  </div>
                  <span className={`badge ${m.available ? 'badge-live' : 'badge-demo'}`}>
                    {m.available ? 'available' : 'not trained'}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </Card>

      <Card title="Data sources and attribution" icon="🔗">
        <div className="table-wrap" style={{ maxHeight: 'none' }}>
          <table className="feature-table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Role</th>
                <th>What it contributes</th>
                <th>Licence</th>
              </tr>
            </thead>
            <tbody>
              {SOURCES.map((s) => (
                <tr key={s.name} style={{ cursor: 'default' }}>
                  <td>
                    <a href={s.url} target="_blank" rel="noreferrer noopener">
                      {s.name}
                    </a>
                  </td>
                  <td className="muted">{s.role}</td>
                  <td className="rationale">{s.gives}</td>
                  <td className="muted small">{s.licence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="notice notice-info mt14">
          None of these services requires a paid API key. FloodSafe claims no ownership of any
          third-party data and reproduces each provider’s attribution requirement above and in the
          page footer.
        </div>
      </Card>
    </div>
  )
}
