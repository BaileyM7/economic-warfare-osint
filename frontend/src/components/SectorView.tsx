import type { SectorAnalysisResponse } from '../types'
import NarrativeCard from './NarrativeCard'

interface Props {
  data: SectorAnalysisResponse
}

const FLAG_EMOJI: Record<string, string> = {
  US: '\u{1F1FA}\u{1F1F8}', CN: '\u{1F1E8}\u{1F1F3}', TW: '\u{1F1F9}\u{1F1FC}', KR: '\u{1F1F0}\u{1F1F7}', NL: '\u{1F1F3}\u{1F1F1}', GB: '\u{1F1EC}\u{1F1E7}',
  DE: '\u{1F1E9}\u{1F1EA}', FR: '\u{1F1EB}\u{1F1F7}', JP: '\u{1F1EF}\u{1F1F5}', RU: '\u{1F1F7}\u{1F1FA}', SA: '\u{1F1F8}\u{1F1E6}', SG: '\u{1F1F8}\u{1F1EC}',
  AU: '\u{1F1E6}\u{1F1FA}', HK: '\u{1F1ED}\u{1F1F0}', IN: '\u{1F1EE}\u{1F1F3}', IT: '\u{1F1EE}\u{1F1F9}', SE: '\u{1F1F8}\u{1F1EA}', FI: '\u{1F1EB}\u{1F1EE}',
  DK: '\u{1F1E9}\u{1F1F0}', CH: '\u{1F1E8}\u{1F1ED}', AE: '\u{1F1E6}\u{1F1EA}', PH: '\u{1F1F5}\u{1F1ED}',
}

export default function SectorView({ data }: Props) {
  const sanctionedPct = data.company_count > 0
    ? Math.round((data.sanctioned_count / data.company_count) * 100)
    : 0

  return (
    <div>
      <NarrativeCard narrative={data.narrative} />

      {/* Summary cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-6">
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Sector</h3>
          <div className="text-2xl font-semibold text-on-surface capitalize">
            {data.sector_key.replace(/_/g, ' ')}
          </div>
          {data.sector.toLowerCase() !== data.sector_key.replace(/_/g, ' ').toLowerCase() && (
            <div className="text-xs text-outline mt-1">Query: {data.sector}</div>
          )}
        </div>

        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Sanctions Exposure</h3>
          <div className={`text-2xl font-semibold ${data.sanctioned_count > 0 ? 'text-error' : 'text-secondary'}`}>
            {data.sanctioned_count} / {data.company_count}
          </div>
          <div className="text-xs text-outline mt-1">Key players with OFAC designation ({sanctionedPct}%)</div>
        </div>
      </div>

      {/* Company table */}
      <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5 mb-6">
        <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Key Players</h3>
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr>
              {['Company', 'Country', 'Ticker', 'Sanctions'].map((h) => (
                <th key={h} className="text-left text-[11px] text-outline uppercase tracking-wider py-2 px-3 bg-surface-container-lowest/30 border-b border-outline-variant/10">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.companies.map((co, i) => (
              <tr key={i} className="border-b border-outline-variant/5">
                <td className="py-2 px-3 text-on-surface-variant">{co.name}</td>
                <td className="py-2 px-3 text-outline">
                  {co.country ? `${FLAG_EMOJI[co.country] ?? ''} ${co.country}` : '—'}
                </td>
                <td className="py-2 px-3 font-mono text-primary">{co.ticker ?? '—'}</td>
                <td className="py-2 px-3">
                  {co.is_sanctioned ? (
                    <span className="inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold bg-error/15 text-error border border-error/30">
                      OFAC {co.sanction_names[0] ? `· ${co.sanction_names[0]}` : ''}
                    </span>
                  ) : (
                    <span className="inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold bg-secondary/15 text-secondary border border-secondary/30">Clear</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Supply chain exposures */}
      {(data.supply_chain_exposures?.length ?? 0) > 0 && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5 mb-6">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Supply chain exposures (trade tools)</h3>
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                {['Commodity', 'HS code', 'US import share %', 'Top suppliers'].map((h) => (
                  <th key={h} className="text-left text-[11px] text-outline uppercase tracking-wider py-2 px-3 bg-surface-container-lowest/30 border-b border-outline-variant/10">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.supply_chain_exposures!.map((row, i) => (
                <tr key={i} className="border-b border-outline-variant/5">
                  <td className="py-2 px-3 text-on-surface-variant">{row.label}</td>
                  <td className="py-2 px-3 font-mono text-outline">{row.commodity_code}</td>
                  <td className="py-2 px-3 text-outline">{row.import_share_pct?.toFixed?.(1) ?? row.import_share_pct ?? '—'}</td>
                  <td className="py-2 px-3 text-outline text-xs">
                    {Array.isArray(row.top_suppliers) && row.top_suppliers.length > 0
                      ? JSON.stringify(row.top_suppliers.slice(0, 3))
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Geopolitical tensions */}
      {(data.geopolitical_tensions?.length ?? 0) > 0 && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5 mb-6">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Geopolitical tensions (GDELT)</h3>
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                {['Pair', 'Events', 'Level', 'Avg tone'].map((h) => (
                  <th key={h} className="text-left text-[11px] text-outline uppercase tracking-wider py-2 px-3 bg-surface-container-lowest/30 border-b border-outline-variant/10">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.geopolitical_tensions!.map((row, i) => (
                <tr key={i} className="border-b border-outline-variant/5">
                  <td className="py-2 px-3 text-on-surface-variant">{row.pair}</td>
                  <td className="py-2 px-3 text-on-surface-variant">{row.event_count}</td>
                  <td className="py-2 px-3 text-on-surface-variant capitalize">{row.tension_level}</td>
                  <td className="py-2 px-3 text-outline">{row.avg_tone != null ? row.avg_tone.toFixed(2) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex gap-2 flex-wrap mt-4">
        {data.sources.map((s, i) => {
          // Sources are now structured objects ({name, url, description}) but
          // older payloads may still be plain strings — handle both.
          const label = typeof s === 'string' ? s : (s as { name: string }).name
          const url = typeof s === 'string' ? undefined : (s as { url?: string }).url
          const cls = "text-[11px] text-outline px-2 py-0.5 border border-outline-variant/10 rounded-full"
          return url ? (
            <a key={i} href={url} target="_blank" rel="noopener noreferrer" className={`${cls} hover:text-primary hover:border-primary/30`}>{label}</a>
          ) : (
            <span key={i} className={cls}>{label}</span>
          )
        })}
      </div>
    </div>
  )
}
