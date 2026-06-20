import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import QueryBox from '../components/QueryBox'
import ProgressPanel from '../components/ProgressPanel'
import SwarmPanel from '../components/SwarmPanel'
import ImpactInfoCards from '../components/ImpactInfoCards'
import ImpactChart from '../components/ImpactChart'
import ProjectionSummary from '../components/ProjectionSummary'
import ComparablesTable from '../components/ComparablesTable'
import EntityGraphSection from '../components/EntityGraphSection'
import PersonView from '../components/PersonView'
import SectorView from '../components/SectorView'
import VesselView from '../components/VesselView'
import OrchestratorView from '../components/OrchestratorView'
import NarrativeCard from '../components/NarrativeCard'
import DebugPanel from '../components/DebugPanel'
import FollowUpBar from '../components/FollowUpBar'
import { useSearchAnalysis } from '../hooks/useSearchAnalysis'
import { createCOA, generateCOAOptions } from '../api'
import type { BriefingSource } from '../types'

export default function SearchPage() {
  const s = useSearchAnalysis()
  const navigate = useNavigate()
  const [creatingCOA, setCreatingCOA] = useState(false)
  // coaPhase exposes which step of the multi-call pipeline we're in so the
  // button label can read 'Generating analyst-grade COA…' (LLM call, ~15-30s)
  // vs 'Saving…' (DB write, fast). Without this the analyst sees a generic
  // 'Creating…' for the full duration and assumes the app has hung.
  const [coaPhase, setCoaPhase] = useState<'idle' | 'generating' | 'saving'>('idle')

  const hasResults = (s.mode === 'company' && s.impactData) ||
    (s.mode === 'person' && s.personData) ||
    (s.mode === 'sector' && s.sectorData) ||
    (s.mode === 'vessel' && s.vesselData) ||
    (s.mode === 'orchestrator' && s.orchestratorData)

  // Store last analysis result for COA generation pre-fill
  useEffect(() => {
    if (!hasResults) return
    const data =
      s.mode === 'company' ? s.impactData :
      s.mode === 'person' ? s.personData :
      s.mode === 'sector' ? s.sectorData :
      s.mode === 'vessel' ? s.vesselData :
      s.mode === 'orchestrator' ? s.orchestratorData : null
    if (data) {
      try {
        sessionStorage.setItem('emissary_last_analysis', JSON.stringify({
          mode: s.mode,
          query: s.query,
          data,
          timestamp: new Date().toISOString(),
        }))
      } catch { /* storage full — ignore */ }
    }
  }, [hasResults, s.mode, s.impactData, s.personData, s.sectorData, s.vesselData, s.orchestratorData, s.query])

  async function handleCreateCOA() {
    setCreatingCOA(true)
    try {
      let name = 'Analysis COA'
      let description = ''
      let targetEntities: string[] = []
      let recommendations: string[] = []
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      let friendlyFire: Record<string, any>[] = []
      let actionType = 'sanction'
      // Sources from the underlying analysis must flow into the COA so that
      // briefings generated from this COA cite real provenance instead of
      // collapsing every claim to a single placeholder [1]. Vessel/orchestrator
      // analyses already produce structured {name,url,description} dicts;
      // sector returns string[] which we normalize into the same shape.
      let sources: BriefingSource[] = []

      const normalizeSources = (raw: unknown): BriefingSource[] => {
        if (!Array.isArray(raw)) return []
        return raw
          .map((s): BriefingSource | null => {
            if (typeof s === 'string') return { name: s }
            if (s && typeof s === 'object' && 'name' in s) {
              const obj = s as Record<string, unknown>
              return {
                name: String(obj.name),
                url: typeof obj.url === 'string' ? obj.url : undefined,
                record_url: typeof obj.record_url === 'string' ? obj.record_url : undefined,
                description: typeof obj.description === 'string' ? obj.description : undefined,
              }
            }
            return null
          })
          .filter((s): s is BriefingSource => s !== null)
      }

      if (s.mode === 'company' && s.impactData) {
        name = `Sanctions Impact: ${s.impactData.target.name || s.impactData.target.ticker}`
        description = s.impactData.narrative || ''
        targetEntities = [s.impactData.target.name || s.impactData.target.ticker]
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        recommendations = (s.impactData as any).recommendations ?? []
        sources = normalizeSources((s.impactData as any).sources)
        actionType = 'sanction'
      } else if (s.mode === 'person' && s.personData) {
        name = `Person Profile: ${s.personData.name}`
        description = s.personData.narrative || ''
        targetEntities = [s.personData.name]
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        recommendations = (s.personData as any).recommendations ?? []
        sources = normalizeSources(s.personData.sources)
        actionType = 'sanction'
      } else if (s.mode === 'sector' && s.sectorData) {
        name = `Sector Analysis: ${s.sectorData.sector}`
        description = s.sectorData.narrative || ''
        targetEntities = s.sectorData.companies.filter(c => c.is_sanctioned).map(c => c.name)
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        recommendations = (s.sectorData as any).recommendations ?? []
        sources = normalizeSources(s.sectorData.sources)
        actionType = 'export_control'
      } else if (s.mode === 'vessel' && s.vesselData) {
        name = `Vessel Intel: ${s.vesselData.vessel.name || 'Unknown'}`
        description = s.vesselData.narrative || ''
        targetEntities = [s.vesselData.vessel.name || 'Unknown Vessel']
        if (s.vesselData.owner_name) targetEntities.push(s.vesselData.owner_name)
        recommendations = s.vesselData.recommendations ?? []
        sources = normalizeSources(s.vesselData.sources)
        actionType = 'sanction'
      } else if (s.mode === 'orchestrator' && s.orchestratorData) {
        name = `Assessment: ${s.orchestratorData.scenario_type.replace(/_/g, ' ')}`
        description = s.orchestratorData.executive_summary || ''
        recommendations = s.orchestratorData.recommendations ?? []
        friendlyFire = s.orchestratorData.friendly_fire ?? []
        sources = normalizeSources(s.orchestratorData.sources)
        actionType = 'sanction'
      }

      // Route through /coa/generate so the COA gets the LLM-authored
      // rationale + "Because:" recommendations + cite_ids — same analyst-grade
      // shape as the workspace's "Generate COA Options" button. We pass the
      // full analysis payload (including sources) and take the first option
      // returned, layering our computed name/targets on top so the COA stays
      // recognizable as "Vessel Intel: …" / "Sector Analysis: …" etc.
      const analysisPayload: Record<string, unknown> = {
        narrative: description,
        recommendations,
        sources_used: sources,
        target_entities: targetEntities,
      }
      // Include the full mode-specific data so the LLM can cite specific entities
      if (s.mode === 'sector' && s.sectorData) analysisPayload.sector_data = s.sectorData
      if (s.mode === 'vessel' && s.vesselData) analysisPayload.vessel_data = s.vesselData
      if (s.mode === 'company' && s.impactData) analysisPayload.impact_data = s.impactData
      if (s.mode === 'person' && s.personData) analysisPayload.person_data = s.personData
      if (s.mode === 'orchestrator' && s.orchestratorData) analysisPayload.assessment_data = s.orchestratorData

      const objective = `Generate analyst-grade COA for: ${name}. Use the analysis sources to justify each recommendation.`
      let opt: Partial<typeof analysisPayload> & {
        name?: string
        description?: string
        action_type?: string
        target_entities?: string[]
        recommendations?: string[]
        friendly_fire?: Record<string, unknown>[]
        expected_effects?: string[]
        sources?: BriefingSource[]
        rationale?: string
        confidence?: number
      } = {}
      try {
        setCoaPhase('generating')
        const options = await generateCOAOptions({
          analysis_data: analysisPayload,
          objective,
        })
        if (Array.isArray(options) && options.length > 0) {
          opt = options[0] as typeof opt
        }
      } catch (err) {
        console.warn('COA generation fell back to plain create:', err)
      }

      setCoaPhase('saving')
      await createCOA({
        // Prefer LLM-generated name when present, fall back to computed.
        name: opt.name || name,
        description: opt.description || description,
        target_entities: (opt.target_entities && opt.target_entities.length > 0) ? opt.target_entities : targetEntities,
        action_type: opt.action_type || actionType,
        recommendations: (opt.recommendations && opt.recommendations.length > 0) ? opt.recommendations : recommendations,
        friendly_fire: (opt.friendly_fire && opt.friendly_fire.length > 0) ? opt.friendly_fire : friendlyFire,
        expected_effects: opt.expected_effects ?? [],
        sources: (opt.sources && opt.sources.length > 0) ? opt.sources : sources,
        rationale: opt.rationale ?? '',
        confidence: opt.confidence ?? null,
        status: 'draft',
      })
      navigate('/coa')
    } catch (err) {
      console.error('Failed to create COA from analysis:', err)
    } finally {
      setCreatingCOA(false)
      setCoaPhase('idle')
    }
  }

  // Friendly label for whichever step of the COA pipeline is running.
  // Shown on every Create-COA button instance (resultsPanel + ImpactInfoCards).
  const coaButtonLabel = coaPhase === 'generating'
    ? 'Generating analyst-grade COA…'
    : coaPhase === 'saving'
      ? 'Saving…'
      : 'Create COA from Analysis'

  return (
    <div className="p-6">
      <QueryBox
        loading={s.loading}
        health={s.health}
        onAnalyze={(entityType, entity, question) => s.runDirectAnalysis(entityType, entity, question)}
        onDeepAnalyze={(question) => s.runOrchestratorAnalysis(question)}
        onClear={s.clearAll}
      />

      {s.mode === 'orchestrator' && (s.swarmEvents.length > 0 || s.loading) && (
        <SwarmPanel events={s.swarmEvents} loading={s.loading} />
      )}

      {s.progress.length > 0 && s.mode !== 'orchestrator' && (
        <ProgressPanel entries={s.progress} loading={s.loading} />
      )}

      {/* Create COA from analysis — shown for non-company, non-person views.
          Company embeds it inside ImpactInfoCards; Person embeds it inline with
          the Risk Factors header (see PersonView). */}
      {/* Orchestrator mode renders this inside OrchestratorView's toolbar (one row with Copy/PDF). */}
      {hasResults && s.mode !== 'company' && s.mode !== 'person' && s.mode !== 'orchestrator' && (
        <div className="flex justify-end mb-4">
          <button
            onClick={handleCreateCOA}
            disabled={creatingCOA}
            className="bg-primary-container text-on-primary-container px-4 py-2 rounded-lg flex items-center gap-2 text-sm font-bold hover:brightness-110 transition-all disabled:opacity-50"
          >
            <span className={`material-symbols-outlined text-base ${creatingCOA ? 'animate-spin' : ''}`}>
              {creatingCOA ? 'progress_activity' : 'add_task'}
            </span>
            {coaButtonLabel}
          </button>
        </div>
      )}

      {/* Company / sanctions impact view */}
      {s.mode === 'company' && s.impactData && (
        <div id="resultsPanel">
          <NarrativeCard narrative={s.impactData.narrative} />
          <ImpactInfoCards
            target={s.impactData.target}
            extra={
              <button
                onClick={handleCreateCOA}
                disabled={creatingCOA}
                className="w-full h-full bg-primary-container text-on-primary-container rounded-xl px-6 py-4 flex items-center justify-center gap-2 text-sm font-bold hover:brightness-110 transition-all disabled:opacity-50"
              >
                <span className={`material-symbols-outlined text-base ${creatingCOA ? 'animate-spin' : ''}`}>
                  {creatingCOA ? 'progress_activity' : 'add_task'}
                </span>
                {coaButtonLabel}
              </button>
            }
          />

          <div className="impact-chart-container bg-surface-container-lowest border border-outline-variant/10 rounded-lg p-5 mb-6">
            <ImpactChart ref={s.chartRef} data={s.impactData} />
          </div>

          {s.impactData.projection.coherence_low && (
            <div style={{
              background: 'rgba(239,177,106,0.1)',
              border: '1px solid rgba(239,177,106,0.35)',
              borderRadius: '6px',
              padding: '10px 14px',
              marginBottom: '16px',
              fontSize: '12px',
              color: '#efb16a',
              display: 'flex',
              alignItems: 'flex-start',
              gap: '8px',
            }}>
              <span style={{ flexShrink: 0, fontWeight: 700 }}>Low Coherence</span>
              <span>
                Comparable cases are split on direction (agreement:{' '}
                {((s.impactData.projection.coherence_score ?? 0) * 100).toFixed(0)}%). The projected
                mean may mask significant divergence — treat confidence bands as indicative only.
              </span>
            </div>
          )}

          <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5" style={{ marginBottom: '24px' }}>
            <h3>Projected Impact Summary</h3>
            <ProjectionSummary summary={s.impactData.projection.summary} />
          </div>

          <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-5">
            <h3>
              Historical Comparable Cases{' '}
              <span style={{ fontSize: '11px', color: 'rgba(255,255,255,0.7)', textTransform: 'none', letterSpacing: 0 }}>
                (click to toggle on chart)
              </span>
              {s.impactData.metadata.sourcing_method && (
                <span style={{
                  marginLeft: '10px',
                  fontSize: '10px',
                  fontWeight: 500,
                  letterSpacing: '0.04em',
                  textTransform: 'uppercase',
                  padding: '2px 7px',
                  borderRadius: '4px',
                  background: s.impactData.metadata.sourcing_method === 'static_fallback'
                    ? 'rgba(31,56,100,0.4)'
                    : 'rgba(67,134,195,0.15)',
                  color: s.impactData.metadata.sourcing_method === 'static_fallback'
                    ? 'rgba(255,255,255,0.7)'
                    : '#4386c3',
                  border: `1px solid ${s.impactData.metadata.sourcing_method === 'static_fallback' ? '#1f3864' : 'rgba(67,134,195,0.3)'}`,
                }}>
                  {s.impactData.metadata.sourcing_method === 'claude' ? 'AI-sourced'
                   : s.impactData.metadata.sourcing_method === 'cache' ? 'AI-sourced (cached)'
                   : 'Reference dataset'}
                </span>
              )}
            </h3>
            <ComparablesTable
              comparables={s.impactData.comparables}
              hidden={s.hiddenDatasets}
              onToggle={s.handleToggle}
            />
          </div>

          <div className="text-[11px] text-outline text-center mt-4">
            Data sources: Yahoo Finance, OFAC SDN, Trade.gov Consolidated Screening List, OpenSanctions
          </div>

          <FollowUpBar
            contextType="company"
            context={{
              target: s.impactData.target,
              projection: {
                summary: s.impactData.projection.summary,
                coherence_score: s.impactData.projection.coherence_score,
                coherence_low: s.impactData.projection.coherence_low,
              },
              comparables: s.impactData.comparables.map((c) => ({
                name: c.name,
                ticker: c.ticker,
                sanction_date: c.sanction_date,
                description: c.description,
                sector: c.sector,
                sanction_type: c.sanction_type,
              })),
              control_comparables: (s.impactData.control_comparables ?? []).map((c) => ({
                name: c.name,
                ticker: c.ticker,
              })),
              narrative: s.impactData.narrative,
              metadata: s.impactData.metadata,
            }}
          />
        </div>
      )}

      {/* Person risk profile view */}
      {s.mode === 'person' && s.personData && (
        <PersonView
          data={s.personData}
          onCreateCOA={handleCreateCOA}
          creatingCOA={creatingCOA}
          coaButtonLabel={coaButtonLabel}
        />
      )}

      {/* Sector analysis view */}
      {s.mode === 'sector' && s.sectorData && (
        <SectorView data={s.sectorData} />
      )}

      {/* Vessel intelligence view */}
      {s.mode === 'vessel' && s.vesselData && (
        <>
          <VesselView data={s.vesselData} onDrillDown={(q) => s.setVesselDrillDown(q)} />
          <FollowUpBar
            contextType="vessel"
            context={{
              vessel: s.vesselData.vessel,
              ownership_chain: s.vesselData.ownership_chain,
              trade_activity: s.vesselData.trade_activity,
              countries_visited: s.vesselData.countries_visited,
              narrative: s.vesselData.narrative,
              is_sanctioned: s.vesselData.is_sanctioned,
              risk_scores: s.vesselData.risk_scores,
            }}
            prefillQuestion={s.vesselDrillDown}
          />
        </>
      )}

      {/* Full orchestrator view */}
      {s.mode === 'orchestrator' && s.orchestratorData && (
        <OrchestratorView
          data={s.orchestratorData}
          onCreateCOA={handleCreateCOA}
          creatingCOA={creatingCOA}
          coaButtonLabel={coaButtonLabel}
        />
      )}

      {/* Entity graph */}
      {s.mode !== 'orchestrator' && s.mode !== 'vessel' && (
        <EntityGraphSection
          graphData={s.graphData}
          graphLoading={s.graphLoading}
          uboOwners={s.uboOwners}
          uboLoading={s.uboLoading}
          uboTargetName={s.uboTargetName}
        />
      )}

      {/* Debug panels */}
      {s.mode === 'company' && s.impactData && (
        <DebugPanel data={s.impactData} label="Raw API Response — sanctions-impact" />
      )}
      {s.mode === 'person' && s.personData && (
        <DebugPanel data={s.personData} label="Raw API Response — person-profile" />
      )}
      {s.mode === 'sector' && s.sectorData && (
        <DebugPanel data={s.sectorData} label="Raw API Response — sector-analysis" />
      )}
      {s.mode === 'vessel' && s.vesselData && (
        <DebugPanel data={s.vesselData} label="Raw API Response — vessel-track" />
      )}
    </div>
  )
}
