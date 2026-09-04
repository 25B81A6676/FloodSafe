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
    <div className="grid" style={{ gap: 14, gridTemplateColumns: 'minmax(0, 1fr)' }}>
      <Card title="How the risk score is produced" icon="🔬">
        <div className="prose">
          <p>
            FloodSafe estimates how <em>susceptible</em> a location currently is to flash flooding by
            fusing five independent open data sources into a single transparent score. It is a
            decision-support prototype, not a validated hydrological forecast.
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

          <h3>What this prototype cannot do</h3>
          <ul>
            <li>It does not simulate hydraulics: no inundation depth, extent or arrival time.</li>
            <li>It is not calibrated against observed flood events, so the score is relative, not probabilistic.</li>
            <li>GloFAS resolves rivers on a ~5 km grid, too coarse for small headwater catchments.</li>
            <li>OpenStreetMap coverage of small streams varies, which affects drainage density.</li>
            <li>It must never be the sole basis for an evacuation decision.</li>
          </ul>
        </div>
      </Card>

      <Card title="Model features and weights" icon="⚖">
        {config.error && <ErrorBox error={config.error} onRetry={config.refresh} />}
        {!cfg && !config.error && <Skeleton height={280} />}
        {cfg && (
          <>
            <div className="row wrap" style={{ gap: 8, marginBottom: 12 }}>
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
                              width: 44,
                              height: 5,
                              borderRadius: 3,
                              background: 'var(--surface-3)',
                              position: 'relative',
                              marginRight: 7,
                              verticalAlign: 'middle',
                            }}
                          >
                            <span
                              style={{
                                position: 'absolute',
                                inset: 0,
                                width: `${(f.weight / 0.16) * 100}%`,
                                background: 'var(--accent)',
                                borderRadius: 3,
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
              <h4 style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.6px', color: 'var(--muted)' }}>
                Risk classes
              </h4>
              <div className="row wrap mt6" style={{ gap: 8 }}>
                {cfg.risk_classes.map((c) => (
                  <span
                    key={c.level}
                    className="risk-pill"
                    style={{ background: c.color, minWidth: 130 }}
                  >
                    {c.label}
                    <span style={{ marginLeft: 'auto', fontFamily: 'var(--mono)' }}>
                      {c.min}–{c.max}
                    </span>
                  </span>
                ))}
              </div>
            </div>

            <div className="mt14">
              <h4 style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.6px', color: 'var(--muted)' }}>
                Model registry
              </h4>
              {cfg.available_models.map((m) => (
                <div className="source-row" key={m.id}>
                  <div className="source-name">
                    <b>
                      {m.name}{' '}
                      {m.active && <span className="chip" style={{ marginLeft: 6 }}>active</span>}
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
