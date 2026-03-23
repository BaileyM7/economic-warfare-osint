import type { AnalyzeResponse, AnalysisStatus, HealthResponse } from './types'

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch('/api/health')
  return res.json()
}

export async function startAnalysis(query: string): Promise<AnalyzeResponse> {
  const res = await fetch('/api/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Failed to start analysis')
  return data
}

export async function getAnalysis(analysisId: string): Promise<AnalysisStatus> {
  const res = await fetch(`/api/analyze/${analysisId}`)
  if (!res.ok) throw new Error('Analysis not found')
  return res.json()
}
