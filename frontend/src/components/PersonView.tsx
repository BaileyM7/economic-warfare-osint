import type { PersonProfileResponse } from '../types'
import FactorCard from './FactorCard'
import NarrativeCard from './NarrativeCard'

interface Props {
  data: PersonProfileResponse
  onCreateCOA?: () => void
  creatingCOA?: boolean
  // Optional rich label so the parent can communicate which phase of the
  // multi-step pipeline we're in (e.g. 'Generating analyst-grade COA…').
  // Falls back to a plain 'Creating…' when not provided.
  coaButtonLabel?: string
}

function ToneBadge({ tone }: { tone: number | null }) {
  if (tone === null) return <span className="inline-block px-2 py-0.5 rounded-full text-[11px] font-medium bg-outline/15 text-outline border border-outline/30">—</span>
  if (tone > 1) return <span className="inline-block px-2 py-0.5 rounded-full text-[11px] font-medium bg-secondary/15 text-secondary border border-secondary/30">+{tone.toFixed(1)}</span>
  if (tone < -1) return <span className="inline-block px-2 py-0.5 rounded-full text-[11px] font-medium bg-error/15 text-error border border-error/30">{tone.toFixed(1)}</span>
  return <span className="inline-block px-2 py-0.5 rounded-full text-[11px] font-medium bg-outline/15 text-outline border border-outline/30">{tone.toFixed(1)}</span>
}

export default function PersonView({ data, onCreateCOA, creatingCOA = false, coaButtonLabel }: Props) {
  return (
    <div>
      <NarrativeCard narrative={data.narrative} />

      {/* Risk factor grid — F1 Sanctions / F2 Corporate / F3 Offshore / F4 News */}
      {data.risk_factors && data.risk_factors.length > 0 && (
        <div className="mb-6">
          <div className="flex items-center justify-between gap-3 mb-3">
            <h3 className="text-sm text-outline uppercase tracking-wider">Risk Factors</h3>
            {onCreateCOA && (
              <button
                onClick={onCreateCOA}
                disabled={creatingCOA}
                className="bg-primary-container text-on-primary-container px-3.5 py-1.5 rounded-lg flex items-center gap-1.5 text-xs font-bold hover:brightness-110 transition-all disabled:opacity-50 whitespace-nowrap"
              >
                <span className={`material-symbols-outlined text-sm ${creatingCOA ? 'animate-spin' : ''}`}>
                  {creatingCOA ? 'progress_activity' : 'add_task'}
                </span>
                {coaButtonLabel ?? (creatingCOA ? 'Creating…' : 'Create COA from Analysis')}
              </button>
            )}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
            {data.risk_factors.map((f, i) => (
              <FactorCard key={`${f.title}-${i}`} factor={f} />
            ))}
          </div>
        </div>
      )}

      {/* Identity + Sanctions status side-by-side */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-6">
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Identity</h3>
          <div className="text-xl font-semibold text-on-surface mb-1.5">{data.name}</div>
          <div className="text-sm text-outline flex gap-4">
            {data.nationality && <span>Nationality: {data.nationality}</span>}
            {data.dob && <span>DOB: {data.dob}</span>}
          </div>
          {data.aliases.length > 0 && (
            <div className="mt-3">
              <div className="text-[11px] text-outline uppercase tracking-wider mb-1.5">Aliases</div>
              {data.aliases.map((a) => (
                <div key={a} className="text-sm text-on-surface-variant mb-0.5">{a}</div>
              ))}
            </div>
          )}
        </div>

        <div
          className={`border rounded-lg p-5 ${
            data.is_sanctioned
              ? 'bg-error/10 border-error/30'
              : 'bg-secondary/10 border-secondary/30'
          }`}
        >
          <h3
            className={`text-sm uppercase tracking-wider mb-3 ${
              data.is_sanctioned ? 'text-error' : 'text-secondary'
            }`}
          >
            Sanctions Status
          </h3>
          <div
            className={`text-xl font-semibold mb-1.5 ${
              data.is_sanctioned ? 'text-error' : 'text-secondary'
            }`}
          >
            {data.is_sanctioned ? 'SANCTIONED' : 'No active designation'}
          </div>
          {data.is_sanctioned && data.sanction_programs.length > 0 ? (
            <div className="mt-3">
              <div className="text-[11px] uppercase tracking-wider mb-1.5 text-error/80">
                Programs
              </div>
              <div className="flex flex-wrap gap-1.5">
                {data.sanction_programs.map((p) => (
                  <span
                    key={p}
                    className="inline-block px-2 py-0.5 rounded-full text-[11px] font-semibold bg-error/15 text-error border border-error/30"
                  >
                    {p}
                  </span>
                ))}
              </div>
            </div>
          ) : (
            <div className="text-sm text-on-surface-variant">
              {data.is_sanctioned
                ? 'SDN / Sanctions list match'
                : 'No OFAC SDN, OpenSanctions, or CSL matches found.'}
            </div>
          )}
        </div>
      </div>

      {/* Offshore connections (only when present) */}
      {data.offshore_connections.length > 0 && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5 mb-6">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Offshore Connections (ICIJ)</h3>
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                <th className="text-left text-[11px] text-outline uppercase tracking-wider py-2 px-3 bg-surface-container-lowest/30 border-b border-outline-variant/10">Entity</th>
                <th className="text-left text-[11px] text-outline uppercase tracking-wider py-2 px-3 bg-surface-container-lowest/30 border-b border-outline-variant/10">Dataset</th>
                <th className="text-left text-[11px] text-outline uppercase tracking-wider py-2 px-3 bg-surface-container-lowest/30 border-b border-outline-variant/10">Jurisdiction</th>
              </tr>
            </thead>
            <tbody>
              {data.offshore_connections.map((c, i) => (
                <tr key={i} className="border-b border-outline-variant/5">
                  <td className="py-2 px-3 text-on-surface-variant">{c.entity}</td>
                  <td className="py-2 px-3 text-outline">{c.dataset}</td>
                  <td className="py-2 px-3 text-outline">{c.jurisdiction || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Corporate affiliations */}
      <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5 mb-6">
        <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Corporate Affiliations (OpenCorporates)</h3>
        {data.affiliations.length > 0 ? (
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                {['Company', 'Role', 'Nationality', 'Status'].map((h) => (
                  <th key={h} className="text-left text-[11px] text-outline uppercase tracking-wider py-2 px-3 bg-surface-container-lowest/30 border-b border-outline-variant/10">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.affiliations.map((a, i) => (
                <tr key={i} className="border-b border-outline-variant/5">
                  <td className="py-2 px-3 text-on-surface-variant">{a.company}</td>
                  <td className="py-2 px-3 text-outline">{a.role}</td>
                  <td className="py-2 px-3 text-outline">{a.nationality || '—'}</td>
                  <td className="py-2 px-3">
                    <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                      a.active
                        ? 'bg-secondary/15 text-secondary border border-secondary/30'
                        : 'bg-error/15 text-error border border-error/30'
                    }`}>
                      {a.active ? 'Active' : 'Former'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="text-sm text-outline italic py-3">No corporate officer records found.</div>
        )}
      </div>

      {/* Recent events */}
      <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5 mb-6">
        <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Recent Coverage (GDELT — last 30 days)</h3>
        {data.recent_events.length > 0 ? (
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                {['Event', 'Date', 'Tone'].map((h) => (
                  <th key={h} className="text-left text-[11px] text-outline uppercase tracking-wider py-2 px-3 bg-surface-container-lowest/30 border-b border-outline-variant/10">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.recent_events.map((ev, i) => (
                <tr key={i} className="border-b border-outline-variant/5">
                  <td className="py-2 px-3">
                    {ev.source
                      ? <a href={ev.source} target="_blank" rel="noreferrer" className="text-primary hover:underline">{ev.title}</a>
                      : <span className="text-on-surface-variant">{ev.title}</span>}
                  </td>
                  <td className="py-2 px-3 text-outline whitespace-nowrap">{ev.date || '—'}</td>
                  <td className="py-2 px-3"><ToneBadge tone={ev.tone} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="text-sm text-outline italic py-3">No recent GDELT coverage in the past 30 days.</div>
        )}
      </div>

      <div className="flex gap-2 flex-wrap mt-4">
        {data.sources.map((s, i) => {
          const label = typeof s === 'string' ? s : (s as { name: string }).name
          return (
            <span key={i} className="text-[11px] text-outline px-2 py-0.5 border border-outline-variant/10 rounded-full">{label}</span>
          )
        })}
      </div>
    </div>
  )
}
