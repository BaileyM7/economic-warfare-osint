import { useRef, useState } from 'react'
import { copyAssessment, downloadAssessmentPdf } from '../lib/exportAssessment'
import type { ImpactAssessmentResult } from '../types'
import DebugPanel from './DebugPanel'
import FollowUpBar from './FollowUpBar'
// The orchestrator entity graph is now rendered by the unified EntityGraphSection
// panel on SearchPage (#34), so this view no longer draws its own graph.

interface Props {
  data: ImpactAssessmentResult
  /** When provided (orchestrator mode), renders "Create COA from Analysis" in the toolbar row. */
  onCreateCOA?: () => void
  creatingCOA?: boolean
  coaButtonLabel?: string
}

const CONFIDENCE_COLORS: Record<string, string> = {
  HIGH:   'text-secondary bg-secondary/10 border-secondary/30',
  MEDIUM: 'text-tertiary bg-tertiary/10 border-tertiary/30',
  LOW:    'text-error bg-error/10 border-error/30',
}

function ConfidenceBadge({ level }: { level: string }) {
  const cls = CONFIDENCE_COLORS[level] ?? CONFIDENCE_COLORS.LOW
  return (
    <span className={`inline-block text-[11px] font-semibold uppercase tracking-wider px-2 py-0.5 border rounded-full ${cls}`}>
      {level}
    </span>
  )
}

export default function OrchestratorView({
  data,
  onCreateCOA,
  creatingCOA,
  coaButtonLabel,
}: Props) {
  const scenarioLabel = data.scenario_type.replace(/_/g, ' ')
  const rootRef = useRef<HTMLDivElement>(null)
  const [copied, setCopied] = useState(false)
  const [pdfBusy, setPdfBusy] = useState(false)

  async function handleCopy() {
    try {
      await copyAssessment(data)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      /* clipboard blocked — ignore */
    }
  }
  async function handlePdf() {
    if (!rootRef.current) return
    setPdfBusy(true)
    try {
      await downloadAssessmentPdf(rootRef.current, `emissary-assessment-${Date.now()}.pdf`)
    } finally {
      setPdfBusy(false)
    }
  }

  return (
    <div ref={rootRef}>
      {/* Action toolbar — Create COA + Copy + PDF on one row (excluded from PDF capture) */}
      <div data-html2canvas-ignore className="flex items-center justify-end gap-2 mb-3">
        {onCreateCOA && (
          <button
            onClick={onCreateCOA}
            disabled={creatingCOA}
            className="bg-primary-container text-on-primary-container px-4 py-1.5 rounded-lg flex items-center gap-2 text-sm font-bold hover:brightness-110 transition-all disabled:opacity-50"
          >
            <span
              className={`material-symbols-outlined text-base ${creatingCOA ? 'animate-spin' : ''}`}
            >
              {creatingCOA ? 'progress_activity' : 'add_task'}
            </span>
            {coaButtonLabel ?? 'Create COA from Analysis'}
          </button>
        )}
        <button
          onClick={handleCopy}
          className="flex items-center gap-1.5 text-xs text-on-surface-variant bg-surface-container hover:bg-surface-container-high border border-outline-variant/30 rounded-lg px-3 py-1.5 transition-all"
        >
          <span className="material-symbols-outlined text-sm">{copied ? 'check' : 'content_copy'}</span>
          {copied ? 'Copied' : 'Copy'}
        </button>
        <button
          onClick={handlePdf}
          disabled={pdfBusy}
          className="flex items-center gap-1.5 text-xs text-on-surface-variant bg-surface-container hover:bg-surface-container-high border border-outline-variant/30 rounded-lg px-3 py-1.5 transition-all disabled:opacity-50"
        >
          <span className="material-symbols-outlined text-sm">picture_as_pdf</span>
          {pdfBusy ? 'Generating…' : 'Download PDF'}
        </button>
      </div>

      {/* Executive summary */}
      <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5 mb-6 border-l-2 border-l-primary">
        <div className="flex items-center justify-between mb-2.5">
          <h3 className="text-sm text-outline uppercase tracking-wider font-semibold">Executive Assessment</h3>
          <span className="text-[11px] text-outline bg-surface-container-high px-2.5 py-1 rounded-full uppercase tracking-wider">
            {scenarioLabel}
          </span>
        </div>
        <p className="text-sm text-on-surface-variant leading-relaxed">
          {data.executive_summary || 'No summary generated.'}
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-6">
        {/* Findings */}
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
          <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Findings ({data.findings.length})</h3>
          {data.findings.length > 0 ? (
            <div className="flex flex-col gap-3 mt-2">
              {data.findings.map((f, i) => (
                <div key={i} className="border-l-2 border-outline-variant/20 pl-3">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[11px] text-outline uppercase tracking-wider">{f.category || 'General'}</span>
                    <ConfidenceBadge level={f.confidence || 'LOW'} />
                  </div>
                  <p className="text-sm text-on-surface-variant leading-relaxed">{f.finding}</p>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-outline italic py-3">No findings returned.</div>
          )}
        </div>

        {/* Friendly fire + recommendations */}
        <div className="flex flex-col gap-5">
          {data.friendly_fire.length > 0 && (
            <div className="bg-error/5 border border-error/30 rounded-lg p-5">
              <h3 className="text-sm text-error uppercase tracking-wider mb-3">Friendly Fire Alerts ({data.friendly_fire.length})</h3>
              <div className="flex flex-col gap-2.5 mt-2">
                {data.friendly_fire.map((ff, i) => (
                  <div key={i}>
                    <div className="text-sm font-semibold text-on-surface">{ff.entity}</div>
                    <div className="text-sm text-outline mt-0.5">
                      {ff.details
                        || [ff.exposure_type, ff.estimated_impact].filter(Boolean).join(' \u00B7 ')
                        || '\u2014'}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {data.recommendations.length > 0 && (
            <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
              <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Recommendations</h3>
              <ul className="mt-2 pl-4 flex flex-col gap-1.5 list-disc">
                {data.recommendations.map((r, i) => (
                  <li key={i} className="text-sm text-on-surface-variant leading-relaxed">{r}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Confidence summary */}
          {Object.keys(data.confidence_summary).length > 0 && (
            <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
              <h3 className="text-sm text-outline uppercase tracking-wider mb-3">Confidence by Domain</h3>
              <table className="w-full border-collapse text-sm mt-2">
                <tbody>
                  {Object.entries(data.confidence_summary).map(([domain, level]) => (
                    <tr key={domain} className="border-b border-outline-variant/5">
                      <td className="py-2 text-outline capitalize">{domain.replace(/_/g, ' ')}</td>
                      <td className="py-2"><ConfidenceBadge level={String(level)} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* Sources */}
      {data.sources.length > 0 && (
        <div className="flex gap-2 flex-wrap mt-2">
          {data.sources.map((s, i) => (
            <span key={i} className="text-[11px] text-outline px-2 py-0.5 border border-outline-variant/10 rounded-full">{s.name}</span>
          ))}
        </div>
      )}

      <FollowUpBar
        contextType="orchestrator"
        context={{
          query: data.query,
          scenario_type: data.scenario_type,
          executive_summary: data.executive_summary,
          findings: data.findings,
          friendly_fire: data.friendly_fire,
          recommendations: data.recommendations,
          confidence_summary: data.confidence_summary,
          tool_results: data.tool_results ?? {},
        }}
      />

      <DebugPanel data={data} label="Raw API Response — orchestrator" />
    </div>
  )
}
