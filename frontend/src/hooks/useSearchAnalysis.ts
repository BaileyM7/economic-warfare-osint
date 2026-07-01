import { useEffect, useRef, useState } from 'react'
import {
  fetchHealth,
  fetchSanctionsImpact,
  fetchEntityGraph,
  resolveEntity,
  fetchPersonProfile,
  fetchSectorAnalysis,
  fetchVesselTrack,
  startOrchestratorAnalysis as apiStartOrchestrator,
  pollAnalysisStatus,
  fetchSayariResolve,
  fetchSayariUBO,
} from '../api'
import { extractTicker } from '../components/QueryBox'
import { assessmentToEntityGraph } from '../lib/assessmentGraph'
import type { ImpactChartHandle } from '../components/ImpactChart'
import type {
  HealthResponse,
  SanctionsImpactResponse,
  EntityGraphResponse,
  ProgressEntry,
  PersonProfileResponse,
  SectorAnalysisResponse,
  VesselTrackResponse,
  ImpactAssessmentResult,
  SayariUBOOwner,
  OrchestratorEvent,
} from '../types'

export type ViewMode = 'company' | 'person' | 'sector' | 'vessel' | 'orchestrator'

function classifyClient(query: string): ViewMode | null {
  const raw = query.trim()
  const digits = raw.replace(/[\s-]/g, '')
  if (/^\d{9}$/.test(digits)) return 'vessel'
  if (/^\d{7}$/.test(digits) || /^imo\s*\d/i.test(raw)) return 'vessel'
  return null
}

export function useSearchAnalysis() {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [progress, setProgress] = useState<ProgressEntry[]>([])
  const [mode, setMode] = useState<ViewMode | null>(null)

  const [impactData, setImpactData] = useState<SanctionsImpactResponse | null>(null)
  const [personData, setPersonData] = useState<PersonProfileResponse | null>(null)
  const [sectorData, setSectorData] = useState<SectorAnalysisResponse | null>(null)
  const [vesselData, setVesselData] = useState<VesselTrackResponse | null>(null)
  const [vesselDrillDown, setVesselDrillDown] = useState('')
  const [orchestratorData, setOrchestratorData] = useState<ImpactAssessmentResult | null>(null)
  const [swarmEvents, setSwarmEvents] = useState<OrchestratorEvent[]>([])
  const [graphData, setGraphData] = useState<EntityGraphResponse | null>(null)
  const [graphLoading, setGraphLoading] = useState(false)

  const [uboOwners, setUboOwners] = useState<SayariUBOOwner[]>([])
  const [uboLoading, setUboLoading] = useState(false)
  const [uboTargetName, setUboTargetName] = useState('')

  const [hiddenDatasets, setHiddenDatasets] = useState<Set<number>>(new Set())
  const chartRef = useRef<ImpactChartHandle>(null)

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => {})
  }, [])

  function addProgress(msg: string, type: ProgressEntry['type'] = 'step') {
    const time = new Date().toLocaleTimeString()
    setProgress((prev) => [...prev, { msg, type, time }])
  }

  function clearAll() {
    setQuery('')
    setLoading(false)
    setProgress([])
    setMode(null)
    setImpactData(null)
    setPersonData(null)
    setSectorData(null)
    setVesselData(null); setVesselDrillDown('')
    setOrchestratorData(null)
    setSwarmEvents([])
    setGraphData(null)
    setGraphLoading(false)
    setUboOwners([])
    setUboLoading(false)
    setUboTargetName('')
    setHiddenDatasets(new Set())
  }

  async function loadEntityGraph(ticker: string) {
    setGraphLoading(true)
    setGraphData(null)
    setUboOwners([])
    setUboLoading(true)
    setUboTargetName('')

    try {
      const data = await fetchEntityGraph(ticker)
      setGraphData(data)
    } catch {
      // non-critical
    } finally {
      setGraphLoading(false)
    }

    try {
      const resolved = await fetchSayariResolve(ticker)
      if (resolved.entities.length > 0) {
        const primaryId = resolved.entities[0].entity_id
        setUboTargetName(resolved.entities[0].label || ticker)
        const uboResult = await fetchSayariUBO(primaryId)
        setUboOwners(uboResult.owners)
      }
    } catch {
      // non-critical
    } finally {
      setUboLoading(false)
    }
  }

  async function startAnalysis(queryOverride?: string) {
    const raw = (queryOverride !== undefined ? queryOverride : query).trim()
    if (!raw) return

    setLoading(true)
    setImpactData(null)
    setPersonData(null)
    setSectorData(null)
    setVesselData(null); setVesselDrillDown('')
    setOrchestratorData(null)
    setGraphData(null)
    setGraphLoading(false)
    setUboOwners([])
    setUboLoading(false)
    setUboTargetName('')
    setProgress([])
    setHiddenDatasets(new Set())
    setMode(null)

    let detectedMode = classifyClient(raw)

    if (detectedMode === 'orchestrator') {
      setLoading(false)
      await runOrchestratorAnalysis(raw)
      return
    }

    // Lookup query (entity identifier) vs. analyst question (full user sentence).
    // The pipeline uses the lookup to find the entity in its database; the
    // analyst question is passed through to COA generation so recommendations
    // reflect the original user intent (e.g. "war in Iran" context).
    let lookupQuery = raw
    const analystQuestion = raw

    if (!detectedMode) {
      addProgress('Classifying query...')
      try {
        const resolution = await resolveEntity(raw)
        detectedMode = resolution.entity_type as ViewMode
        addProgress(`Identified as: ${resolution.entity_type} — "${resolution.entity_name}" (${(resolution.confidence * 100).toFixed(0)}% confidence)`)
        // Substitute the cleanly extracted entity name for the lookup query.
        // Without this, downstream parsing is brittle:
        //   - vessel/person search the raw sentence as if it were a name
        //   - extractTicker() regex on a sentence can match nonsense capital
        //     letters as tickers (e.g. "I" from "I do about...")
        //   - sector search has fuzzy matching but still benefits from a clean
        //     keyword over a long sentence
        if (resolution.entity_name && resolution.entity_name.trim().length > 0) {
          lookupQuery = resolution.entity_name
        }
      } catch {
        detectedMode = 'company'
      }
    }

    if (detectedMode === 'orchestrator') {
      setLoading(false)
      await runOrchestratorAnalysis(raw)
      return
    }

    setMode(detectedMode)

    if (detectedMode === 'company') {
      await runCompanyAnalysis(lookupQuery, analystQuestion)
    } else if (detectedMode === 'person') {
      await runPersonAnalysis(lookupQuery, analystQuestion)
    } else if (detectedMode === 'sector') {
      await runSectorAnalysis(lookupQuery, analystQuestion)
    } else if (detectedMode === 'vessel') {
      await runVesselAnalysis(lookupQuery, analystQuestion)
    }

    setLoading(false)
  }

  async function runCompanyAnalysis(raw: string, analystQuestion: string = raw) {
    const ticker = extractTicker(raw)
    addProgress(`Resolving ticker: ${ticker}`)
    addProgress('Checking sanctions status (OFAC, OpenSanctions, Trade.gov CSL)...')
    addProgress('Fetching historical comparable data...')
    try {
      const data = await fetchSanctionsImpact(ticker, analystQuestion)
      addProgress(`Found ${data.metadata.comparable_count} comparable sanctions cases`)
      addProgress('Computing projection with confidence interval...')
      addProgress('Done!', 'done')
      setImpactData(data)
      loadEntityGraph(ticker)
    } catch (e) {
      addProgress(`Error: ${(e as Error).message}`, 'error')
    }
  }

  async function runPersonAnalysis(raw: string, analystQuestion: string = raw) {
    addProgress(`Building risk profile for: ${raw}`)
    addProgress('Searching OpenSanctions, OFAC, OpenCorporates, ICIJ Offshore Leaks, GDELT...')
    try {
      const data = await fetchPersonProfile(raw, analystQuestion)
      addProgress('Done!', 'done')
      setPersonData(data)
      if (data.graph.nodes.length > 0) {
        setGraphData({
          nodes: data.graph.nodes,
          edges: data.graph.edges,
          meta: { query: raw, node_count: data.graph.nodes.length, edge_count: data.graph.edges.length },
        })
      }
    } catch (e) {
      addProgress(`Error: ${(e as Error).message}`, 'error')
    }
  }

  async function runSectorAnalysis(raw: string, analystQuestion: string = raw) {
    addProgress(`Analyzing sector: ${raw}`)
    addProgress('Checking key players for sanctions exposure (OFAC)...')
    try {
      const data = await fetchSectorAnalysis(raw, analystQuestion)
      addProgress(
        `${data.sanctioned_count} of ${data.company_count} key players have OFAC designations`,
        'done',
      )
      setSectorData(data)
      if (data.graph.nodes.length > 0) {
        setGraphData({
          nodes: data.graph.nodes,
          edges: data.graph.edges,
          meta: { query: raw, node_count: data.graph.nodes.length, edge_count: data.graph.edges.length },
        })
      }
    } catch (e) {
      addProgress(`Error: ${(e as Error).message}`, 'error')
    }
  }

  async function runVesselAnalysis(raw: string, analystQuestion: string = raw) {
    addProgress(`Tracking vessel: ${raw}`)
    addProgress('Checking OFAC sanctions list, AIS database, OpenSanctions...')
    try {
      const data = await fetchVesselTrack(raw, analystQuestion)
      addProgress(
        data.is_sanctioned ? 'OFAC match found — vessel is sanctioned!' : 'No OFAC designation found.',
        data.is_sanctioned ? 'error' : 'done',
      )
      setVesselData(data)
      if (data.graph.nodes.length > 0) {
        setGraphData({
          nodes: data.graph.nodes,
          edges: data.graph.edges,
          meta: { query: raw, node_count: data.graph.nodes.length, edge_count: data.graph.edges.length },
        })
      }
    } catch (e) {
      addProgress(`Error: ${(e as Error).message}`, 'error')
    }
  }

  async function runOrchestratorAnalysis(rawOverride?: string) {
    const raw = (rawOverride !== undefined ? rawOverride : query).trim()
    if (!raw) {
      setProgress([
        {
          msg: 'Enter a question in the search box first, then click Deep Analysis (or Ctrl+Enter).',
          type: 'error',
          time: new Date().toLocaleTimeString(),
        },
      ])
      return
    }

    setLoading(true)
    setImpactData(null)
    setPersonData(null)
    setSectorData(null)
    setVesselData(null); setVesselDrillDown('')
    setOrchestratorData(null)
    setGraphData(null)
    setGraphLoading(false)
    setProgress([])
    setSwarmEvents([])
    setHiddenDatasets(new Set())
    setMode('orchestrator')

    addProgress('Submitting to orchestrator pipeline...')
    try {
      const { analysis_id } = await apiStartOrchestrator(raw)
      addProgress(`Pipeline started (ID: ${analysis_id})`)

      let done = false
      while (!done) {
        // ~0.9s cadence per the Phase 3 contract — fast enough for the swarm to
        // feel live without hammering the backend.
        await new Promise<void>((r) => setTimeout(r, 900))
        const status = await pollAnalysisStatus(analysis_id)

        const entries: ProgressEntry[] = status.progress.map((msg) => ({
          msg,
          type: (msg.toLowerCase().startsWith('error') ? 'error'
                : msg.toLowerCase().includes('complete') || msg === 'Done.' ? 'done'
                : 'step') as ProgressEntry['type'],
          time: '',
        }))
        setProgress(entries)
        // Append-only event stream — backend returns the full list each poll.
        if (status.events) setSwarmEvents(status.events)

        if (status.status === 'completed' && status.result) {
          setOrchestratorData(status.result)
          // Unify the graph (#34): feed the assessment's entity_graph into the
          // same EntityGraphSection panel the typed searches use.
          setGraphData(assessmentToEntityGraph(status.result, raw))
          done = true
        } else if (status.status === 'failed') {
          setProgress((prev) => [
            ...prev,
            { msg: `Failed: ${status.error ?? 'Unknown error'}`, type: 'error', time: '' },
          ])
          done = true
        }
      }
    } catch (e) {
      addProgress(`Error: ${(e as Error).message}`, 'error')
    } finally {
      setLoading(false)
    }
  }

  /**
   * Direct analysis path — used when the user explicitly picks an entity type
   * (Company / Person / Sector / Vessel) instead of letting the LLM resolver
   * guess from a free-text query. Skips classifyClient + resolveEntity entirely.
   */
  async function runDirectAnalysis(
    entityType: 'company' | 'person' | 'sector' | 'vessel',
    entity: string,
    question: string,
  ) {
    const cleanEntity = entity.trim()
    if (!cleanEntity) return
    const cleanQuestion = question.trim() || cleanEntity

    setLoading(true)
    setImpactData(null)
    setPersonData(null)
    setSectorData(null)
    setVesselData(null); setVesselDrillDown('')
    setOrchestratorData(null)
    setGraphData(null)
    setGraphLoading(false)
    setUboOwners([])
    setUboLoading(false)
    setUboTargetName('')
    setProgress([])
    setHiddenDatasets(new Set())
    setMode(entityType)
    setQuery(cleanQuestion)

    if (entityType === 'company') {
      await runCompanyAnalysis(cleanEntity, cleanQuestion)
    } else if (entityType === 'person') {
      await runPersonAnalysis(cleanEntity, cleanQuestion)
    } else if (entityType === 'sector') {
      await runSectorAnalysis(cleanEntity, cleanQuestion)
    } else if (entityType === 'vessel') {
      await runVesselAnalysis(cleanEntity, cleanQuestion)
    }

    setLoading(false)
  }

  function handleToggle(idx: number) {
    chartRef.current?.toggleDataset(idx)
    setHiddenDatasets((prev) => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  return {
    // State
    query, loading, health, progress, mode,
    impactData, personData, sectorData, vesselData, vesselDrillDown,
    orchestratorData, swarmEvents, graphData, graphLoading,
    uboOwners, uboLoading, uboTargetName,
    hiddenDatasets, chartRef,
    // Actions
    setQuery, setVesselDrillDown,
    startAnalysis, runDirectAnalysis, runOrchestratorAnalysis, clearAll, handleToggle,
  }
}
