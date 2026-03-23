import type { HealthResponse } from '../types'

const EXAMPLES = [
  "What happens if we sanction Fujian Jinhua?",
  "What is the supply chain impact of sanctioning Norinco's subsidiary in Malaysia?",
  "China is investing $30M in a port in Sri Lanka — how do we intersect?",
  "What are the downstream effects of sanctioning Russian oil exports?",
]

interface Props {
  query: string
  isRunning: boolean
  health: HealthResponse | null
  onQueryChange: (q: string) => void
  onAnalyze: () => void
  onClear: () => void
}

export default function QueryBox({ query, isRunning, health, onQueryChange, onAnalyze, onClear }: Props) {
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      onAnalyze()
    }
  }

  return (
    <div className="query-box">
      <textarea
        id="query"
        rows={2}
        placeholder="Ask a question... e.g., What happens if we sanction Fujian Jinhua?"
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        onKeyDown={handleKeyDown}
      />
      <div className="btn-row">
        <button className="btn btn-primary" disabled={isRunning} onClick={onAnalyze}>
          Analyze
        </button>
        <button className="btn btn-secondary" onClick={onClear}>
          Clear
        </button>
        {health && (
          <span>
            {health.status === 'ok' ? (
              <span className="status-badge ok">API Connected</span>
            ) : (
              <span className="status-badge error">{health.issues.join(', ')}</span>
            )}
          </span>
        )}
      </div>
      <div className="examples">
        {EXAMPLES.map((ex) => (
          <span key={ex} className="example-chip" onClick={() => onQueryChange(ex)}>
            {ex}
          </span>
        ))}
      </div>
    </div>
  )
}
