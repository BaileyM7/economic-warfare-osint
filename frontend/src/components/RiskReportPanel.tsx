import type { EntityRiskReport, RiskIndicator } from '../types'

interface Props {
  report: EntityRiskReport | null
  loading: boolean
  entityName: string | null
  onClose: () => void
}

function RiskLevelBadge({ level }: { level: 'HIGH' | 'MEDIUM' | 'LOW' }) {
  const cls: Record<string, string> = {
    HIGH:   'bg-error/15 text-error border-error/30',
    MEDIUM: 'bg-tertiary/15 text-tertiary border-tertiary/30',
    LOW:    'bg-secondary/15 text-secondary border-secondary/30',
  }
  return (
    <span className={`inline-block px-2.5 py-0.5 rounded-full text-[11px] font-bold uppercase tracking-wider border ${cls[level]}`}>
      {level} RISK
    </span>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[11px] text-outline uppercase tracking-wider mb-2 pb-1.5 border-b border-outline-variant/10 mt-4">
      {children}
    </div>
  )
}

function KV({ label, children, highlight }: { label: string; children: React.ReactNode; highlight?: boolean }) {
  return (
    <div className="flex justify-between items-start py-1.5 border-b border-outline-variant/5 gap-3 text-xs">
      <span className="text-outline shrink-0">{label}</span>
      <span className={`text-right break-words ${highlight ? 'text-on-surface font-semibold' : 'text-on-surface-variant'}`}>{children}</span>
    </div>
  )
}

function IndicatorRow({ ind }: { ind: RiskIndicator }) {
  const colorCls = { high: 'text-error', medium: 'text-tertiary', low: 'text-secondary' }[ind.severity]
  return (
    <div className="flex justify-between items-start py-1.5 border-b border-outline-variant/10 gap-3">
      <span className="text-xs text-outline shrink-0">{ind.label}</span>
      <span className={`text-xs text-right break-words ${colorCls} ${ind.severity === 'high' ? 'font-semibold' : ''}`}>
        {ind.value}
      </span>
    </div>
  )
}

function fmtMC(mc: number | null): string {
  if (!mc) return '—'
  if (mc >= 1e12) return `$${(mc / 1e12).toFixed(2)}T`
  if (mc >= 1e9)  return `$${(mc / 1e9).toFixed(1)}B`
  if (mc >= 1e6)  return `$${(mc / 1e6).toFixed(0)}M`
  return `$${mc.toLocaleString()}`
}

function fmtUSD(v: number | null): string {
  if (!v) return '—'
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`
  return `$${v.toLocaleString()}`
}

function RangeBar({ low, high, current }: { low: number; high: number; current: number }) {
  const span = high - low || 1
  const pct = Math.max(0, Math.min(100, ((current - low) / span) * 100))
  return (
    <div className="mt-2 mb-1">
      <div className="relative h-1.5 bg-outline-variant/20 rounded-full">
        <div className="absolute left-0 top-0 h-full bg-primary rounded-full min-w-1" style={{ width: `${pct}%` }} />
        <div className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 w-2.5 h-2.5 bg-white rounded-full border-2 border-primary" style={{ left: `${pct}%` }} />
      </div>
      <div className="flex justify-between text-[10px] text-outline mt-1">
        <span>${low.toFixed(2)} 52w low</span>
        <span>52w high ${high.toFixed(2)}</span>
      </div>
    </div>
  )
}

export default function RiskReportPanel({ report, loading, entityName, onClose }: Props) {
  if (!report && !loading && !entityName) return null

  return (
    <>
      <div className="fixed inset-0 bg-black/35 z-[200]" onClick={onClose} />
      <div className="fixed top-0 right-0 bottom-0 w-[380px] max-w-[95vw] bg-surface-container-low border-l border-outline-variant/10 z-[201] flex flex-col shadow-[-4px_0_24px_rgba(0,0,0,0.4)] animate-[slideInRight_0.2s_ease]">
        {/* Header */}
        <div className="flex items-start gap-3 p-4 border-b border-outline-variant/10 shrink-0">
          <div className="flex-1 min-w-0">
            <div className="text-[11px] text-outline uppercase tracking-wider mb-1">Entity Risk Report</div>
            <div className="text-base font-semibold text-on-surface overflow-hidden text-ellipsis whitespace-nowrap">{entityName ?? '\u2026'}</div>
          </div>
          <button onClick={onClose} className="bg-transparent border-none text-outline cursor-pointer text-xl leading-none p-1 shrink-0 hover:text-on-surface" aria-label="Close">&times;</button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {loading && !report && (
            <div className="flex items-center text-outline text-sm py-5">
              <span className="inline-block w-5 h-5 border-[3px] border-outline-variant border-t-primary rounded-full animate-spin mr-2.5" />
              Analyzing {entityName}&hellip;
            </div>
          )}

          {report && (
            <>
              {/* Badges */}
              <div className="flex gap-2 items-center mb-3.5 flex-wrap">
                <RiskLevelBadge level={report.risk_level} />
                <span className={`inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wider border ${
                  report.is_sanctioned
                    ? 'bg-error/15 text-error border-error/30'
                    : 'bg-secondary/15 text-secondary border-secondary/30'
                }`}>
                  {report.is_sanctioned ? 'Sanctioned' : 'Not Sanctioned'}
                </span>
                {report.market_info?.sector && (
                  <span className="text-[11px] text-outline">{report.market_info.sector}</span>
                )}
              </div>

              {/* Narrative */}
              {report.narrative && (
                <div className="bg-surface-container border border-outline-variant/10 rounded-lg p-3 text-sm leading-relaxed text-on-surface-variant mb-1">
                  {report.narrative}
                </div>
              )}

              {/* Risk Indicators */}
              {report.risk_indicators.length > 0 && (
                <>
                  <SectionTitle>Risk Indicators</SectionTitle>
                  {report.risk_indicators.map((ind, i) => <IndicatorRow key={i} ind={ind} />)}
                </>
              )}

              {/* Sanctions Details */}
              {report.sanction_details?.length > 0 && (
                <>
                  <SectionTitle>Sanctions Matches</SectionTitle>
                  {report.sanction_details.map((d, i) => (
                    <div key={i} className="bg-error/5 border border-error/15 rounded-lg p-2 mb-1.5">
                      <div className="text-xs font-semibold text-error">{d.name}</div>
                      <div className="text-[11px] text-outline mt-0.5">
                        Score: {(d.score * 100).toFixed(0)}%
                        {d.programs.length > 0 && <> &middot; {d.programs.join(', ')}</>}
                      </div>
                      {d.remarks && <div className="text-[11px] text-outline mt-1 italic">{d.remarks}</div>}
                    </div>
                  ))}
                </>
              )}

              {/* Sanction programs chips */}
              {!report.sanction_details?.length && report.sanction_programs.length > 0 && (
                <>
                  <SectionTitle>Sanction Programs</SectionTitle>
                  <div className="flex flex-wrap gap-1.5 mt-1">
                    {report.sanction_programs.map((p) => (
                      <span key={p} className="bg-error/10 border border-error/25 text-error rounded px-2 py-0.5 text-[11px] font-medium">{p}</span>
                    ))}
                  </div>
                </>
              )}

              {/* Market data */}
              {report.market_info && (
                <>
                  <SectionTitle>Market Data ({report.market_info.ticker})</SectionTitle>
                  {report.market_info.current_price != null && (
                    <KV label="Price" highlight>
                      ${report.market_info.current_price.toFixed(2)}
                      {report.market_info.change_pct != null && (
                        <span className={`ml-2 text-[11px] ${report.market_info.change_pct >= 0 ? 'text-secondary' : 'text-error'}`}>
                          {report.market_info.change_pct >= 0 ? '+' : ''}{report.market_info.change_pct.toFixed(2)}%
                        </span>
                      )}
                    </KV>
                  )}
                  {report.market_info.market_cap != null && <KV label="Market Cap">{fmtMC(report.market_info.market_cap)}</KV>}
                  {report.market_info.industry && <KV label="Industry">{report.market_info.industry}</KV>}
                  {report.market_info.exchange && <KV label="Exchange">{report.market_info.exchange}</KV>}

                  {report.market_info.fifty_two_week_low != null && report.market_info.fifty_two_week_high != null && report.market_info.current_price != null && (
                    <div className="mt-2">
                      <div className="text-[11px] text-outline mb-0.5">
                        52-Week Range
                        {report.market_info.pct_from_52w_high != null && (
                          <span className={`ml-2 ${report.market_info.pct_from_52w_high < -20 ? 'text-error' : 'text-outline'}`}>
                            ({report.market_info.pct_from_52w_high > 0 ? '+' : ''}{report.market_info.pct_from_52w_high.toFixed(1)}% vs high)
                          </span>
                        )}
                      </div>
                      <RangeBar low={report.market_info.fifty_two_week_low} high={report.market_info.fifty_two_week_high} current={report.market_info.current_price} />
                    </div>
                  )}

                  {(report.market_info.analyst_target || report.market_info.analyst_recommendation) && (
                    <div className="mt-2 bg-primary/5 border border-primary/15 rounded-lg p-2.5">
                      <div className="text-[11px] text-outline mb-1">
                        Analyst Consensus{report.market_info.analyst_count ? ` (${report.market_info.analyst_count} analysts)` : ''}
                      </div>
                      <div className="flex gap-4 items-center">
                        {report.market_info.analyst_recommendation && (
                          <span className="text-sm font-semibold text-primary uppercase">{report.market_info.analyst_recommendation}</span>
                        )}
                        {report.market_info.analyst_target && report.market_info.current_price && (
                          <span className="text-xs text-outline">
                            Target: ${report.market_info.analyst_target.toFixed(2)}
                            <span className={`ml-1.5 ${report.market_info.analyst_target > report.market_info.current_price ? 'text-secondary' : 'text-error'}`}>
                              ({report.market_info.analyst_target > report.market_info.current_price ? '+' : ''}{((report.market_info.analyst_target / report.market_info.current_price - 1) * 100).toFixed(1)}% implied)
                            </span>
                          </span>
                        )}
                      </div>
                    </div>
                  )}

                  {report.market_info.description && (
                    <div className="text-xs text-outline mt-2.5 leading-relaxed italic">{report.market_info.description}</div>
                  )}
                </>
              )}

              {/* Institutional exposure */}
              {report.exposure && report.exposure.top_holders.length > 0 && (
                <>
                  <SectionTitle>Institutional Exposure</SectionTitle>
                  {report.exposure.total_institutional_usd != null && (
                    <div className="text-xs text-outline mb-2">
                      Total tracked: <strong className="text-on-surface-variant">{fmtUSD(report.exposure.total_institutional_usd)}</strong>
                      {report.exposure.pension_count > 0 && (
                        <span className="ml-2 text-tertiary">&middot; {report.exposure.pension_count} pension/sovereign fund(s)</span>
                      )}
                    </div>
                  )}
                  <table className="w-full border-collapse text-xs">
                    <thead>
                      <tr>
                        <th className="text-left text-[10px] text-outline uppercase font-medium py-1 border-b border-outline-variant/10">Holder</th>
                        <th className="text-right text-[10px] text-outline uppercase font-medium py-1 border-b border-outline-variant/10">% Held</th>
                        <th className="text-right text-[10px] text-outline uppercase font-medium py-1 border-b border-outline-variant/10">Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.exposure.top_holders.map((h, i) => (
                        <tr key={i} className="border-b border-outline-variant/5">
                          <td className={`py-1.5 ${h.is_pension ? 'text-tertiary' : 'text-on-surface-variant'}`}>
                            {h.name}
                            {h.is_pension && <span className="ml-1 text-[9px] bg-tertiary/15 text-tertiary border border-tertiary/25 rounded px-1 py-px">PENSION</span>}
                          </td>
                          <td className="py-1.5 text-right text-on-surface-variant">{h.pct_held != null ? `${h.pct_held.toFixed(2)}%` : '—'}</td>
                          <td className="py-1.5 text-right text-outline">{fmtUSD(h.value_usd)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}

              {/* Officers */}
              {report.officers?.length > 0 && (
                <>
                  <SectionTitle>Key Officers</SectionTitle>
                  {report.officers.map((o, i) => (
                    <div key={i} className="flex justify-between py-1.5 border-b border-outline-variant/5 text-xs">
                      <span className="text-on-surface-variant">{o.name}</span>
                      <span className="text-outline">{o.role}</span>
                    </div>
                  ))}
                </>
              )}

              {/* Offshore / ICIJ */}
              {report.offshore_flags?.length > 0 && (
                <>
                  <SectionTitle>ICIJ Offshore Connections</SectionTitle>
                  {report.offshore_flags.map((f, i) => (
                    <div key={i} className="bg-[rgba(239,177,106,0.08)] border border-[rgba(239,177,106,0.2)] rounded p-2 mb-1 text-xs">
                      <span className="text-[#efb16a] font-medium">{f.entity}</span>
                      {f.jurisdiction && <span className="text-outline ml-2">{f.jurisdiction}</span>}
                      {f.dataset && <span className="text-outline ml-2 text-[11px]">{f.dataset}</span>}
                    </div>
                  ))}
                </>
              )}

              {/* Corporate info */}
              {Object.keys(report.corporate_info).length > 0 && (
                <>
                  <SectionTitle>Corporate Record</SectionTitle>
                  {report.corporate_info.legal_name && <KV label="Legal Name">{report.corporate_info.legal_name}</KV>}
                  {report.corporate_info.lei && <KV label="LEI"><span className="font-mono text-[11px]">{report.corporate_info.lei}</span></KV>}
                  {report.corporate_info.status && (
                    <KV label="Status">
                      <span className={report.corporate_info.status === 'ACTIVE' || report.corporate_info.status === 'ISSUED' ? 'text-secondary' : 'text-outline'}>
                        {report.corporate_info.status}
                      </span>
                    </KV>
                  )}
                  {report.corporate_info.incorporation_date && <KV label="Incorporated">{report.corporate_info.incorporation_date}</KV>}
                  {report.corporate_info.registered_address && (
                    <KV label="Address"><span className="text-[11px]">{report.corporate_info.registered_address}</span></KV>
                  )}
                </>
              )}

              {/* Sources */}
              {report.sources.length > 0 && (
                <div className="text-[11px] text-outline mt-4 pt-2.5 border-t border-outline-variant/10">
                  Sources: {report.sources.join(' \u00B7 ')}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </>
  )
}
