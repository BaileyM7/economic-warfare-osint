import { useCallback, useEffect, useState } from 'react'
import EntityGraphSection from '../components/EntityGraphSection'
import { fetchKnowledgeGraph, deleteKnowledgeEntity } from '../api'
import type { EntityGraphResponse } from '../types'

// Phase 4 (#37): the shared knowledge graph explorer. Renders the team's saved
// entities/relationships (/api/knowledge/graph) through the same graph panel used
// on Search — so the view-switcher (Clustered / Focus) and node actions (Find
// Similar, Discover, Risk Report) come for free — plus a Remove action.
export default function KnowledgeGraphPage() {
  const [data, setData] = useState<EntityGraphResponse | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await fetchKnowledgeGraph())
    } catch (e) {
      console.warn('Failed to load knowledge graph:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const handleRemove = useCallback(
    async (entityId: string) => {
      try {
        await deleteKnowledgeEntity(entityId)
        await load()
      } catch (e) {
        console.warn('Failed to remove entity:', e)
      }
    },
    [load],
  )

  const count = data?.nodes.length ?? 0

  return (
    <div className="p-6">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h1 className="text-2xl font-headline font-bold uppercase tracking-widest text-on-surface">
            Knowledge Graph
          </h1>
          <p className="text-xs text-outline mt-1 max-w-2xl">
            The team&rsquo;s shared, persistent entity graph. Anything saved with
            &ldquo;Save to Graph&rdquo; across the app appears here. Switch between Clustered and
            Focus views, expand via Sayari, run risk reports, or remove entities.
          </p>
        </div>
        <button
          onClick={() => void load()}
          disabled={loading}
          className="flex items-center gap-1.5 bg-primary-container text-on-primary-container text-xs font-bold uppercase tracking-widest px-3.5 py-2 rounded-lg hover:brightness-110 disabled:opacity-50"
        >
          <span className="material-symbols-outlined text-sm">refresh</span>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {count === 0 && !loading ? (
        <div className="flex flex-col items-center justify-center h-[420px] text-center border border-outline-variant/10 rounded-lg bg-surface-container-lowest">
          <span className="material-symbols-outlined text-4xl text-outline mb-3">hub</span>
          <p className="text-sm text-on-surface-variant">The shared knowledge graph is empty.</p>
          <p className="text-xs text-outline mt-1 max-w-md">
            Run a search, select an entity, and click <b>Save to Graph</b> to start building the
            team&rsquo;s shared graph. Saved entities and their relationships will show up here.
          </p>
        </div>
      ) : (
        <EntityGraphSection
          graphData={data}
          graphLoading={loading}
          onRemoveNode={handleRemove}
          heading="Shared Knowledge Graph"
        />
      )}
    </div>
  )
}
