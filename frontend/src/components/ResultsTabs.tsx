import type { GraphData, TabId } from '../types'
import GraphViewer from './GraphViewer'
import JsonViewer from './JsonViewer'
import ReportViewer from './ReportViewer'

interface Props {
  activeTab: TabId
  markdown: string | null
  result: Record<string, unknown> | null
  graphData: GraphData | null
  onTabChange: (tab: TabId) => void
}

const TABS: { id: TabId; label: string }[] = [
  { id: 'report', label: 'Report' },
  { id: 'graph', label: 'Entity Graph' },
  { id: 'json', label: 'Raw JSON' },
]

export default function ResultsTabs({ activeTab, markdown, result, graphData, onTabChange }: Props) {
  return (
    <div className="results active">
      <div className="tabs">
        {TABS.map((tab) => (
          <div
            key={tab.id}
            className={`tab ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => onTabChange(tab.id)}
          >
            {tab.label}
          </div>
        ))}
      </div>

      <div className={`tab-content ${activeTab === 'report' ? 'active' : ''}`} id="tab-report">
        {markdown && <ReportViewer markdown={markdown} />}
      </div>

      <div className={`tab-content ${activeTab === 'graph' ? 'active' : ''}`} id="tab-graph">
        {graphData && <GraphViewer graphData={graphData} />}
      </div>

      <div className={`tab-content ${activeTab === 'json' ? 'active' : ''}`} id="tab-json">
        {result && <JsonViewer data={result} />}
      </div>
    </div>
  )
}
