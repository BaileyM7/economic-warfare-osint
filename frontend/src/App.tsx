import { useEffect, useRef, useState } from 'react'
import { fetchHealth, getAnalysis, startAnalysis } from './api'
import Header from './components/Header'
import ProgressPanel, { type ProgressEntry } from './components/ProgressPanel'
import QueryBox from './components/QueryBox'
import ResultsTabs from './components/ResultsTabs'
import type { GraphData, HealthResponse, TabId, WsMessage } from './types'

export default function App() {
  const [query, setQuery] = useState('')
  const [isRunning, setIsRunning] = useState(false)
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [progress, setProgress] = useState<ProgressEntry[]>([])
  const [showProgress, setShowProgress] = useState(false)
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const [markdown, setMarkdown] = useState<string | null>(null)
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [showResults, setShowResults] = useState(false)
  const [activeTab, setActiveTab] = useState<TabId>('report')

  const wsRef = useRef<WebSocket | null>(null)
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isRunningRef = useRef(false)

  // Keep ref in sync for use inside closures
  isRunningRef.current = isRunning

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => {})
  }, [])

  function addProgress(text: string, type: ProgressEntry['type'] = 'step') {
    const time = new Date().toLocaleTimeString()
    setProgress((prev) => [...prev, { text, type, time }])
  }

  function showResultsData(data: { result: Record<string, unknown>; markdown: string; graph_data: GraphData }) {
    setResult(data.result)
    setMarkdown(data.markdown)
    setGraphData(data.graph_data)
    setShowResults(true)
  }

  function connectWS(analysisId: string) {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${location.host}/ws/analyze/${analysisId}`)
    wsRef.current = ws

    ws.onmessage = (event) => {
      const msg: WsMessage = JSON.parse(event.data)
      if (msg.type === 'progress') {
        addProgress(msg.message, 'step')
      } else if (msg.type === 'complete') {
        addProgress('Analysis complete!', 'done')
        setIsRunning(false)
        showResultsData(msg)
      } else if (msg.type === 'error') {
        addProgress(`Error: ${msg.error}`, 'error')
        setIsRunning(false)
      }
    }

    ws.onerror = () => {
      pollAnalysis(analysisId)
    }

    ws.onclose = () => {
      if (isRunningRef.current) {
        pollAnalysis(analysisId)
      }
    }
  }

  function pollAnalysis(analysisId: string) {
    const poll = async () => {
      try {
        const data = await getAnalysis(analysisId)

        // Rebuild progress log from server state
        setProgress((prev) => {
          const rebuiltProgress = data.progress.map((msg, idx) => {
            const existing = prev[idx]
            const keepExistingTime = existing && existing.text === msg
            return {
              text: msg,
              type: (msg.includes('Error') || msg.includes('failed') ? 'error' : 'step') as ProgressEntry['type'],
              time: keepExistingTime ? existing.time : new Date().toLocaleTimeString(),
            }
          })
          return rebuiltProgress
        })

        if (data.status === 'complete' && data.result && data.markdown && data.graph_data) {
          addProgress('Analysis complete!', 'done')
          setIsRunning(false)
          showResultsData({ result: data.result, markdown: data.markdown, graph_data: data.graph_data })
          return
        } else if (data.status === 'error') {
          addProgress(`Error: ${data.error || 'Unknown'}`, 'error')
          setIsRunning(false)
          return
        }

        pollTimerRef.current = setTimeout(poll, 2000)
      } catch (e) {
        addProgress(`Polling error: ${(e as Error).message}`, 'error')
      }
    }
    poll()
  }

  async function handleAnalyze() {
    const trimmed = query.trim()
    if (!trimmed) return

    // Reset state
    setIsRunning(true)
    setShowProgress(true)
    setShowResults(false)
    setProgress([])
    setResult(null)
    setMarkdown(null)
    setGraphData(null)
    setActiveTab('report')

    // Clear any existing poll timer
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current)
    }
    if (wsRef.current) {
      wsRef.current.close()
    }

    addProgress('Submitting query...', 'step')

    try {
      const data = await startAnalysis(trimmed)
      addProgress(`Analysis started (ID: ${data.analysis_id})`, 'step')
      connectWS(data.analysis_id)
    } catch (e) {
      addProgress(`Connection error: ${(e as Error).message}`, 'error')
      setIsRunning(false)
    }
  }

  function handleClear() {
    setQuery('')
    setShowProgress(false)
    setShowResults(false)
    setProgress([])
    setResult(null)
    setMarkdown(null)
    setGraphData(null)
    setIsRunning(false)
    if (wsRef.current) wsRef.current.close()
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
  }

  return (
    <>
      <Header />
      <div className="main">
        <QueryBox
          query={query}
          isRunning={isRunning}
          health={health}
          onQueryChange={setQuery}
          onAnalyze={handleAnalyze}
          onClear={handleClear}
        />
        {showProgress && (
          <ProgressPanel visible={showProgress} isRunning={isRunning} progress={progress} />
        )}
        {showResults && markdown && result && (
          <ResultsTabs
            activeTab={activeTab}
            markdown={markdown}
            result={result}
            graphData={graphData}
            onTabChange={setActiveTab}
          />
        )}
      </div>
    </>
  )
}
