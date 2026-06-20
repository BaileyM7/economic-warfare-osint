import { useState } from 'react'
import type { VesselTrackResponse, OwnershipLink } from '../types'
import GraphViewer from './GraphViewer'
import AISRouteMap from './AISRouteMap'
import SankeyChart from './SankeyChart'

interface Props {
  data: VesselTrackResponse
  onDrillDown?: (question: string) => void
}

function RelLabel({ rel }: { rel: string }) {
  const labels: Record<string, string> = {
    registered_owner: 'Registered Owner',
    owner: 'Owner',
    beneficial_owner: 'Beneficial Owner',
    operator: 'Operator',
    builder: 'Builder',
    manager: 'Manager',
    charterer: 'Charterer',
  }
  return <span className="text-[11px] text-outline">{labels[rel] || rel || 'Related'}</span>
}

function OwnershipNode({ link }: { link: OwnershipLink }) {
  const icon = link.entity_type === 'person' ? '\u{1F464}' : '\u{1F3E2}'
  const color = link.entity_type === 'person' ? 'text-[#0092ff]' : 'text-primary'
  const pct = link.ownership_percentage ? ` (${link.ownership_percentage}%)` : ''
  return (
    <div
      className="p-2.5 border border-outline-variant/10 rounded-lg my-0.5 bg-surface-container-lowest"
      style={{ marginLeft: `${(link.depth - 1) * 24}px` }}
    >
      <span className={color}>{icon}</span>{' '}
      <strong className="text-on-surface">{link.name}</strong>{pct}
      {link.is_sanctioned && (
        <span className="inline-block ml-2 px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-error/15 text-error border border-error/30">SANCTIONED</span>
      )}
      {link.is_pep && (
        <span className="inline-block ml-2 px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-[rgba(239,177,106,0.15)] text-[#efb16a] border border-[rgba(239,177,106,0.3)]">PEP</span>
      )}
      <div className="text-[11px] text-outline">
        <RelLabel rel={link.relationship_type} /> &middot; {link.country || 'Unknown'}
      </div>
    </div>
  )
}

function cpiColor(score: number): string {
  if (score > 60) return 'text-secondary'
  if (score >= 30) return 'text-tertiary'
  return 'text-error'
}

function baselColor(score: number): string {
  if (score < 5) return 'text-secondary'
  if (score <= 7) return 'text-tertiary'
  return 'text-error'
}

export default function VesselView({ data, onDrillDown }: Props) {
  const v = data.vessel
  const [graphTab, setGraphTab] = useState<'ownership' | 'trade'>('ownership')

  return (
    <div className="flex flex-col gap-6">
      {/* 1. Vessel Stats Grid */}
      <div className="grid grid-cols-3 gap-4 max-md:grid-cols-2">
        {[
          { label: 'Vessel Name', value: v.name || 'Unknown' },
          { label: 'IMO', value: v.imo || '\u2014' },
          { label: 'MMSI', value: v.mmsi || '\u2014' },
          { label: 'Flag', value: v.flag || '\u2014' },
          { label: 'Type', value: v.vessel_type || '\u2014' },
          { label: 'OFAC Status', value: data.is_sanctioned ? 'SANCTIONED' : 'Clear', highlight: data.is_sanctioned ? 'text-error' : 'text-secondary' },
          { label: 'Speed', value: v.speed != null ? `${v.speed} kn` : '\u2014' },
          { label: 'Destination', value: v.destination || '\u2014' },
          { label: 'Owner', value: data.owner_name || '\u2014' },
          ...(data.risk_scores?.cpi_score != null ? [{ label: 'CPI Score', value: String(data.risk_scores.cpi_score), highlight: cpiColor(data.risk_scores.cpi_score) }] : []),
          ...(data.risk_scores?.basel_aml != null ? [{ label: 'Basel AML', value: String(data.risk_scores.basel_aml), highlight: baselColor(data.risk_scores.basel_aml) }] : []),
        ].map((stat) => (
          <div key={stat.label} className="bg-surface-container-lowest border border-outline-variant/10 rounded-lg p-3.5 text-center">
            <div className="text-[10px] text-outline uppercase tracking-wider">{stat.label}</div>
            <div className={`text-lg font-semibold mt-1 ${stat.highlight || 'text-on-surface'}`}>{stat.value}</div>
          </div>
        ))}
      </div>

      {/* 2. Sanctions Matches */}
      {data.sanctions_matches && data.sanctions_matches.length > 0 && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Sanctions Matches</h3>
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                {['Name', 'Score', 'Programs'].map((h) => (
                  <th key={h} className="text-left text-[11px] text-outline uppercase tracking-wider py-2 px-3 bg-surface-container-lowest/30 border-b border-outline-variant/10">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.sanctions_matches.map((m, i) => (
                <tr key={i} className="border-b border-outline-variant/5">
                  <td className="py-2 px-3 text-on-surface-variant">{m.name}</td>
                  <td className="py-2 px-3 text-on-surface-variant">{(m.score * 100).toFixed(0)}%</td>
                  <td className="py-2 px-3 text-outline">{m.programs.join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 3. Risk Assessment */}
      {data.narrative && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Risk Assessment</h3>
          <p className="text-sm text-on-surface leading-relaxed">{data.narrative}</p>
        </div>
      )}

      {/* 4. Recommended Courses of Action */}
      {data.recommendations && data.recommendations.length > 0 && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Recommended Courses of Action</h3>
          <ol className="pl-5 text-on-surface-variant leading-relaxed list-decimal">
            {data.recommendations.map((rec, i) => (
              <li key={i} className="text-sm mb-1">{rec}</li>
            ))}
          </ol>
        </div>
      )}

      {/* 5. Countries Visited */}
      {data.countries_visited && data.countries_visited.length > 0 && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Countries / Regions Visited</h3>
          <div className="flex flex-wrap gap-1.5 mt-2">
            {data.countries_visited.map((c, i) => (
              <span key={i} className={`rounded-full px-3 py-1 text-xs border ${
                c.startsWith('(')
                  ? 'bg-surface-container border-outline-variant/20 text-outline'
                  : 'bg-primary/10 border-primary/20 text-primary'
              }`}>
                {c}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 6. Ownership Chain */}
      {data.ownership_chain && data.ownership_chain.length > 0 && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">
            Beneficial Ownership Chain <span className="text-primary font-normal text-[11px]">(Sayari Graph)</span>
          </h3>
          <div className="mt-2">
            {data.ownership_chain.map((link, i) => (
              <OwnershipNode key={link.entity_id || i} link={link} />
            ))}
          </div>
        </div>
      )}

      {/* 7. Trade Activity */}
      {data.trade_activity && data.trade_activity.records && data.trade_activity.records.length > 0 && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">
            Trade Activity <span className="text-primary font-normal text-[11px]">(Sayari Graph)</span>
          </h3>
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                {['Date', 'From', 'To', 'Commodity'].map((h) => (
                  <th key={h} className="text-left text-[11px] text-outline uppercase tracking-wider py-2 px-3 bg-surface-container-lowest/30 border-b border-outline-variant/10">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.trade_activity.records.slice(0, 10).map((r, i) => (
                <tr key={i} className="border-b border-outline-variant/5">
                  <td className="py-2 px-3 text-on-surface-variant">{r.date || '\u2014'}</td>
                  <td className="py-2 px-3 text-on-surface-variant">{r.departure_country || '\u2014'}</td>
                  <td className="py-2 px-3 text-on-surface-variant">{r.arrival_country || '\u2014'}</td>
                  <td className="py-2 px-3 text-outline text-[11px]">{r.hs_description || r.hs_code || '\u2014'}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* HS code pills */}
          {data.trade_activity.top_hs_codes && data.trade_activity.top_hs_codes.length > 0 && (
            <div className="mt-3">
              <span className="text-[11px] text-outline uppercase tracking-wider">Top Commodities</span>
              <div className="flex flex-wrap gap-1.5 mt-1.5">
                {data.trade_activity.top_hs_codes.map((hs, i) => (
                  <span
                    key={i}
                    onClick={() => onDrillDown?.(`What are the forced labor, sanctions, and supply chain risks associated with international trade in ${hs.description || hs.code}?`)}
                    className="bg-surface-container border border-outline-variant/20 rounded-full px-2.5 py-0.5 text-[11px] text-outline cursor-pointer hover:border-primary hover:text-primary transition-colors"
                  >
                    {hs.description || hs.code}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Country pills */}
          {data.trade_activity.trade_countries && data.trade_activity.trade_countries.length > 0 && (
            <div className="mt-3">
              <span className="text-[11px] text-outline uppercase tracking-wider">Trade Countries</span>
              <div className="flex flex-wrap gap-1.5 mt-1.5">
                {data.trade_activity.trade_countries.map((c, i) => (
                  <span
                    key={i}
                    onClick={() => onDrillDown?.(`What are the sanctions risks and geopolitical factors affecting trade with ${c}?`)}
                    className="bg-surface-container border border-outline-variant/20 rounded-full px-2.5 py-0.5 text-[11px] text-primary cursor-pointer hover:border-primary transition-colors"
                  >
                    {c}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* 8. Sankey Trade Flow */}
      {data.trade_activity?.sankey_flows && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Trade Flow Diagram</h3>
          <SankeyChart flows={data.trade_activity.sankey_flows} />
        </div>
      )}

      {/* 9. AIS Route Map */}
      {data.route_history && data.route_history.length > 0 && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">AIS Route Map</h3>
          <AISRouteMap points={data.route_history} vesselName={v.name} />
        </div>
      )}

      {/* 10. AIS Position History */}
      {data.route_history && data.route_history.length > 0 && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">AIS Position History ({data.route_history.length} points)</h3>
          <div className="max-h-[340px] overflow-y-auto rounded-lg border border-outline-variant/10">
            <table className="w-full border-collapse text-sm">
              <thead className="sticky top-0 bg-surface-container-low z-[1]">
                <tr>
                  {['Time', 'Lat', 'Lon', 'Speed'].map((h) => (
                    <th key={h} className="text-left text-[11px] text-outline uppercase tracking-wider py-2 px-3 border-b border-outline-variant/10">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.route_history.map((pt, i) => (
                  <tr key={i} className="border-b border-outline-variant/5">
                    <td className="py-2 px-3 text-on-surface-variant">{pt.ts ? new Date(pt.ts * 1000).toLocaleString() : '-'}</td>
                    <td className="py-2 px-3 text-on-surface-variant">{pt.lat.toFixed(4)}</td>
                    <td className="py-2 px-3 text-on-surface-variant">{pt.lon.toFixed(4)}</td>
                    <td className="py-2 px-3 text-on-surface-variant">{pt.speed} kn</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 11. Tabbed Graph Pane */}
      {((data.graph?.nodes?.length ?? 0) > 0 || (data.trade_graph?.nodes?.length ?? 0) > 0) && (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
          <div className="flex gap-2 mb-3">
            {(['ownership', 'trade'] as const).map((tab) => (
              <button
                key={tab}
                className={`px-4 py-1.5 text-sm rounded-lg border transition-colors ${
                  graphTab === tab
                    ? 'bg-primary/15 text-primary border-primary'
                    : 'bg-surface-container text-outline border-outline-variant/20 hover:border-primary hover:text-primary'
                }`}
                onClick={() => setGraphTab(tab)}
              >
                {tab === 'ownership' ? 'Ownership & Sanctions' : 'Trade Network'}
              </button>
            ))}
          </div>

          {/* Legend */}
          <div className="flex flex-wrap gap-3 mb-3">
            {graphTab === 'ownership' ? (
              <>
                {[
                  { label: 'Vessel', color: '#a9d8fb' },
                  { label: 'Company', color: '#4386c3' },
                  { label: 'Person / UBO', color: '#0092ff' },
                  { label: 'Flag State', color: '#ff5a58' },
                  { label: 'Sanctions', color: '#d23c3a' },
                ].map((l) => (
                  <div key={l.label} className="flex items-center gap-1.5 text-xs text-outline">
                    <span className="w-3 h-3 rounded-full shrink-0" style={{ background: l.color }} />
                    {l.label}
                  </div>
                ))}
              </>
            ) : (
              <>
                {[
                  { label: 'Vessel', color: '#a9d8fb' },
                  { label: 'Trade Partner', color: '#4386c3' },
                  { label: 'Risk Flagged', color: '#d23c3a' },
                ].map((l) => (
                  <div key={l.label} className="flex items-center gap-1.5 text-xs text-outline">
                    <span className="w-3 h-3 rounded-full shrink-0" style={{ background: l.color }} />
                    {l.label}
                  </div>
                ))}
              </>
            )}
          </div>

          <div className="h-[400px]">
            {graphTab === 'ownership' && data.graph?.nodes?.length ? (
              <GraphViewer nodes={data.graph.nodes} edges={data.graph.edges} />
            ) : graphTab === 'trade' && data.trade_graph?.nodes?.length ? (
              <GraphViewer nodes={data.trade_graph.nodes} edges={data.trade_graph.edges} />
            ) : (
              <div className="flex items-center justify-center h-full text-outline text-sm">
                No graph data available for this view.
              </div>
            )}
          </div>
        </div>
      )}

      {/* 12. Source Chips */}
      {data.sources && data.sources.length > 0 && (
        <div className="flex gap-2 flex-wrap mt-2">
          {data.sources.map((s, i) => {
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
      )}
    </div>
  )
}
