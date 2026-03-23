export interface HealthResponse {
  status: 'ok' | 'misconfigured'
  issues: string[]
  model: string
  tools_available: boolean
}

export interface AnalyzeResponse {
  analysis_id: string
  status: string
}

export interface GraphNode {
  id: string
  label: string
  group: string
  title: string
  color: string
}

export interface GraphEdge {
  from: string
  to: string
  label: string
  arrows: string
  dashes: boolean
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface AnalysisStatus {
  analysis_id: string
  status: string
  progress: string[]
  result: Record<string, unknown> | null
  markdown: string | null
  graph_data: GraphData | null
  error: string | null
}

export type TabId = 'report' | 'graph' | 'json'

export interface WsProgressMessage {
  type: 'progress'
  message: string
}

export interface WsCompleteMessage {
  type: 'complete'
  result: Record<string, unknown>
  markdown: string
  graph_data: GraphData
}

export interface WsErrorMessage {
  type: 'error'
  error: string
}

export type WsMessage = WsProgressMessage | WsCompleteMessage | WsErrorMessage
