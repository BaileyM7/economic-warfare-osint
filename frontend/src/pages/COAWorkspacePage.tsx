import { useState, useEffect, useCallback, type ReactNode } from 'react'
import {
  DndContext,
  DragEndEvent,
  PointerSensor,
  useSensor,
  useSensors,
  useDroppable,
  useDraggable,
} from '@dnd-kit/core'
import { useNavigate } from 'react-router-dom'
import {
  fetchCOAs,
  createCOA,
  updateCOA,
  deleteCOA,
  generateCOAOptions,
  generateBriefing,
} from '../api'
import type { COA } from '../types'
import FollowUpBar from '../components/FollowUpBar'

/**
 * Render a string containing inline `[N]` citation markers as React nodes,
 * turning each `[N]` into an anchor that scrolls to `#coa-src-N` (the COA's
 * Sources section entry). Plain text segments pass through unchanged.
 *
 * Example: `"Issue WRO — Because: Vietnam supplier flagged [3]"` becomes a
 * fragment with the `[3]` rendered as a clickable footnote anchor.
 */
function renderWithCites(text: string): ReactNode {
  const parts = text.split(/(\[\d+\])/g)
  return parts.map((part, i) => {
    const m = part.match(/^\[(\d+)\]$/)
    if (m) {
      const n = m[1]
      return (
        <a key={i} href={`#coa-src-${n}`} className="briefing-cite">
          [{n}]
        </a>
      )
    }
    return <span key={i}>{part}</span>
  })
}

const ACTION_TYPE_OPTIONS = [
  'sanction',
  'tariff',
  'trade_restriction',
  'asset_freeze',
  'diplomatic',
  'cyber',
  'financial',
  'other',
] as const

interface COAEditForm {
  name: string
  description: string
  action_type: string
  confidence: string
  target_entities: string
  recommendations: string
  friendly_fire: string
  expected_effects: string
}

function coaToEditForm(coa: COA): COAEditForm {
  return {
    name: coa.name ?? '',
    description: coa.description ?? '',
    action_type: coa.action_type ?? 'sanction',
    confidence: coa.confidence != null ? String(coa.confidence) : '',
    target_entities: (coa.target_entities ?? []).join(', '),
    recommendations: (coa.recommendations ?? []).join('\n'),
    friendly_fire: JSON.stringify(coa.friendly_fire ?? [], null, 2),
    expected_effects: (coa.expected_effects ?? []).join('\n'),
  }
}

const COLUMNS: { label: string; status: COA['status'] }[] = [
  { label: 'Draft', status: 'draft' },
  { label: 'Under Review', status: 'under_review' },
  { label: 'Approved', status: 'approved' },
  { label: 'Executing', status: 'executing' },
  { label: 'Assessed', status: 'assessed' },
]

const STATUS_NEXT: Record<string, COA['status'] | null> = {
  draft: 'under_review',
  under_review: 'approved',
  approved: 'executing',
  executing: 'assessed',
  assessed: null,
}

const STATUS_COLORS: Record<string, string> = {
  draft: 'bg-surface-container-high text-on-surface',
  under_review: 'bg-yellow-900/30 text-yellow-300',
  approved: 'bg-emerald-900/30 text-emerald-300',
  executing: 'bg-blue-900/30 text-blue-300',
  assessed: 'bg-purple-900/30 text-purple-300',
}

function confidenceDisplay(confidence: number | null) {
  if (confidence === null) return { label: 'N/A', cls: 'text-outline' }
  if (confidence > 0.7) return { label: `HIGH (${(confidence * 100).toFixed(0)}%)`, cls: 'text-emerald-400' }
  if (confidence > 0.4) return { label: `MED (${(confidence * 100).toFixed(0)}%)`, cls: 'text-yellow-400' }
  return { label: `LOW (${(confidence * 100).toFixed(0)}%)`, cls: 'text-red-400' }
}

function statusLabel(status: string) {
  return status.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

// ---------- Draggable Card ----------

function COACard({
  coa,
  isSelected,
  onClick,
}: {
  coa: COA
  isSelected: boolean
  onClick: () => void
}) {
  const { attributes, listeners, setNodeRef, transform, isDragging } =
    useDraggable({ id: coa.id })

  const conf = confidenceDisplay(coa.confidence)

  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      onClick={onClick}
      style={{
        transform: transform
          ? `translate3d(${transform.x}px, ${transform.y}px, 0)`
          : undefined,
        opacity: isDragging ? 0.5 : 1,
      }}
      className={`cursor-grab active:cursor-grabbing rounded-lg border p-3 transition-colors select-none ${
        isSelected
          ? 'border-primary bg-primary-container/15'
          : 'border-outline-variant/15 bg-surface-container-lowest hover:border-outline-variant/30'
      }`}
    >
      <p className="text-sm font-bold text-on-surface truncate">{coa.name}</p>

      <div className="flex items-center gap-2 mt-1.5">
        {coa.action_type && (
          <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-surface-container-high text-on-surface-variant">
            {coa.action_type}
          </span>
        )}
        <span className={`text-[10px] font-bold ${conf.cls}`}>
          {conf.label}
        </span>
      </div>
    </div>
  )
}

// ---------- Droppable Column ----------

function KanbanColumn({
  status,
  label,
  coas,
  selectedId,
  onSelect,
}: {
  status: string
  label: string
  coas: COA[]
  selectedId: string | null
  onSelect: (coa: COA) => void
}) {
  const { setNodeRef, isOver } = useDroppable({ id: status })

  return (
    <div className="min-w-[260px] flex-1 flex flex-col gap-3">
      <div className="flex items-center justify-between px-1">
        <span className="text-[10px] font-headline font-bold uppercase tracking-[0.2em] text-outline">
          {label} ({coas.length})
        </span>
      </div>

      <div
        ref={setNodeRef}
        className={`flex-1 flex flex-col gap-2 rounded-lg p-2 transition-colors min-h-[80px] ${
          isOver ? 'bg-primary-container/10 ring-1 ring-primary/30' : ''
        }`}
      >
        {coas.length === 0 ? (
          <div className="h-20 border-2 border-dashed border-outline-variant/10 rounded-lg flex items-center justify-center">
            <span className="text-[10px] text-outline font-bold">
              NO ACTIONS
            </span>
          </div>
        ) : (
          coas.map((coa) => (
            <COACard
              key={coa.id}
              coa={coa}
              isSelected={selectedId === coa.id}
              onClick={() => onSelect(coa)}
            />
          ))
        )}
      </div>
    </div>
  )
}

// ---------- Modal Backdrop ----------

function Modal({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
}) {
  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="bg-surface-container rounded-2xl border border-outline-variant/15 shadow-2xl w-full max-w-lg max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-5 border-b border-outline-variant/10">
          <h2 className="text-base font-headline font-bold text-on-surface">
            {title}
          </h2>
          <button
            onClick={onClose}
            className="text-outline hover:text-on-surface transition-colors"
          >
            <span className="material-symbols-outlined text-xl">close</span>
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  )
}

// ---------- Main Page ----------

export default function COAWorkspacePage() {
  const [coas, setCoas] = useState<COA[]>([])
  const [selectedCoa, setSelectedCoa] = useState<COA | null>(null)
  const [loading, setLoading] = useState(false)

  // Inline edit state for detail panel
  const [editing, setEditing] = useState(false)
  const [editForm, setEditForm] = useState<COAEditForm | null>(null)
  const [editError, setEditError] = useState<string | null>(null)
  const [savingEdit, setSavingEdit] = useState(false)

  // Generate modal
  const [showGenerateModal, setShowGenerateModal] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [generateObjective, setGenerateObjective] = useState('')
  const [generatedOptions, setGeneratedOptions] = useState<Partial<COA>[]>([])

  // Create modal
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [createForm, setCreateForm] = useState({
    name: '',
    description: '',
    action_type: 'sanction',
  })

  const navigate = useNavigate()
  const [generatingBrief, setGeneratingBrief] = useState(false)

  async function handleGenerateBriefing(coaId: string) {
    setGeneratingBrief(true)
    try {
      await generateBriefing({ coa_id: coaId, briefing_type: 'coa_brief' })
      navigate('/briefings')
    } catch (err) {
      console.error('Failed to generate briefing:', err)
    } finally {
      setGeneratingBrief(false)
    }
  }

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  )

  const loadCoas = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchCOAs()
      setCoas(data)
    } catch (err) {
      console.error('Failed to fetch COAs', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadCoas()
  }, [loadCoas])

  // Keep selectedCoa in sync after refresh
  useEffect(() => {
    if (selectedCoa) {
      const fresh = coas.find((c) => c.id === selectedCoa.id)
      if (fresh) setSelectedCoa(fresh)
      else setSelectedCoa(null)
    }
  }, [coas])

  // Exit edit mode whenever selection changes to a different COA
  useEffect(() => {
    setEditing(false)
    setEditForm(null)
    setEditError(null)
  }, [selectedCoa?.id])

  // ----- Drag & Drop -----

  async function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over) return
    const coaId = active.id as string
    const newStatus = over.id as COA['status']
    const coa = coas.find((c) => c.id === coaId)
    if (!coa || coa.status === newStatus) return

    try {
      await updateCOA(coaId, { status: newStatus })
      await loadCoas()
    } catch (err) {
      console.error('Failed to update COA status', err)
    }
  }

  // ----- Generate -----

  async function handleGenerate() {
    if (!generateObjective.trim()) return
    setGenerating(true)
    try {
      // Include last analysis data if available
      let analysisData: Record<string, unknown> | undefined
      try {
        const stored = sessionStorage.getItem('emissary_last_analysis')
        if (stored) analysisData = JSON.parse(stored).data
      } catch { /* ignore */ }
      const options = await generateCOAOptions({
        objective: generateObjective.trim(),
        analysis_data: analysisData,
      })
      setGeneratedOptions(options)
    } catch (err) {
      console.error('Failed to generate COA options', err)
    } finally {
      setGenerating(false)
    }
  }

  async function acceptOption(opt: Partial<COA>) {
    try {
      await createCOA(opt)
      setGeneratedOptions((prev) => prev.filter((o) => o !== opt))
      await loadCoas()
    } catch (err) {
      console.error('Failed to create COA from option', err)
    }
  }

  // ----- Create -----

  async function handleCreate() {
    if (!createForm.name.trim()) return
    try {
      await createCOA({
        name: createForm.name.trim(),
        description: createForm.description.trim(),
        action_type: createForm.action_type,
        status: 'draft',
      })
      setShowCreateModal(false)
      setCreateForm({ name: '', description: '', action_type: 'sanction' })
      await loadCoas()
    } catch (err) {
      console.error('Failed to create COA', err)
    }
  }

  // ----- Delete -----

  async function handleDelete(id: string) {
    try {
      await deleteCOA(id)
      if (selectedCoa?.id === id) setSelectedCoa(null)
      await loadCoas()
    } catch (err) {
      console.error('Failed to delete COA', err)
    }
  }

  // ----- Advance Status -----

  async function handleAdvanceStatus(coa: COA) {
    const next = STATUS_NEXT[coa.status]
    if (!next) return
    try {
      await updateCOA(coa.id, { status: next })
      await loadCoas()
    } catch (err) {
      console.error('Failed to advance COA status', err)
    }
  }

  // ----- Inline Edit -----

  function handleStartEdit() {
    if (!selectedCoa) return
    setEditForm(coaToEditForm(selectedCoa))
    setEditError(null)
    setEditing(true)
  }

  function handleCancelEdit() {
    setEditing(false)
    setEditForm(null)
    setEditError(null)
  }

  async function handleSaveEdit() {
    if (!selectedCoa || !editForm) return

    // Parse friendly_fire JSON
    let friendlyFire: Record<string, unknown>[]
    try {
      const parsed = JSON.parse(editForm.friendly_fire || '[]')
      if (!Array.isArray(parsed)) {
        setEditError('Friendly Fire must be a JSON array.')
        return
      }
      friendlyFire = parsed as Record<string, unknown>[]
    } catch {
      setEditError('Friendly Fire is not valid JSON.')
      return
    }

    // Parse confidence
    let confidence: number | null = null
    if (editForm.confidence.trim() !== '') {
      const n = parseFloat(editForm.confidence)
      if (isNaN(n) || n < 0 || n > 1) {
        setEditError('Confidence must be a number between 0 and 1.')
        return
      }
      confidence = n
    }

    const payload: Partial<COA> = {
      name: editForm.name.trim(),
      description: editForm.description,
      action_type: editForm.action_type,
      confidence,
      target_entities: editForm.target_entities
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
      recommendations: editForm.recommendations
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
      expected_effects: editForm.expected_effects
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
      friendly_fire: friendlyFire,
    }

    setSavingEdit(true)
    setEditError(null)
    try {
      const updated = await updateCOA(selectedCoa.id, payload)
      setSelectedCoa(updated)
      setCoas((prev) => prev.map((c) => (c.id === updated.id ? updated : c)))
      setEditing(false)
      setEditForm(null)
    } catch (err) {
      console.error('Failed to update COA', err)
      setEditError((err as Error).message || 'Failed to save changes.')
    } finally {
      setSavingEdit(false)
    }
  }

  // ----- Render -----

  return (
    <div className="h-[calc(100vh-48px)] flex">
      {/* Kanban Board Area */}
      <section className="flex-1 flex flex-col overflow-hidden">
        <header className="p-6 flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-headline font-bold text-on-surface tracking-tight">
              Course of Action Workspace
            </h1>
            <p className="text-sm text-outline font-body mt-1">
              Modeling economic and tactical escalation paths for Global
              Sentinel.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => {
                setShowGenerateModal(true)
                setGeneratedOptions([])
                // Pre-fill from last search analysis if available
                try {
                  const stored = sessionStorage.getItem('emissary_last_analysis')
                  if (stored) {
                    const { query, mode } = JSON.parse(stored)
                    setGenerateObjective(`Based on ${mode} analysis of "${query}", generate strategic courses of action.`)
                  } else {
                    setGenerateObjective('')
                  }
                } catch {
                  setGenerateObjective('')
                }
              }}
              className="bg-primary-container text-on-primary-container px-4 py-2 rounded-lg flex items-center gap-2 text-sm font-bold hover:brightness-110 transition-all"
            >
              <span className="material-symbols-outlined text-lg">bolt</span>
              Generate COA Options
            </button>
            <button
              onClick={() => {
                setShowCreateModal(true)
                setCreateForm({ name: '', description: '', action_type: 'sanction' })
              }}
              className="bg-surface-container-high text-on-surface px-3 py-2 rounded-lg flex items-center gap-1.5 text-sm font-bold hover:brightness-110 transition-all border border-outline-variant/15"
            >
              <span className="material-symbols-outlined text-lg">add</span>
              Create COA
            </button>
          </div>
        </header>

        {/* Kanban Columns */}
        <div data-vn="coa-board" className="flex-1 overflow-x-auto px-6 pb-6 flex gap-4 items-start">
          {loading && coas.length === 0 ? (
            <div className="flex-1 flex items-center justify-center">
              <span className="material-symbols-outlined text-3xl text-outline animate-spin">
                progress_activity
              </span>
            </div>
          ) : !loading && coas.length === 0 ? (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center max-w-md">
                <span className="material-symbols-outlined text-5xl text-outline block mb-4">account_tree</span>
                <h3 className="text-lg font-headline font-bold text-on-surface mb-2">No Courses of Action Yet</h3>
                <p className="text-sm text-outline mb-4">Run an analysis on the Search page, then click "Create COA from Analysis" to get started. Or use the buttons above to generate COA options with AI or create one manually.</p>
                <button onClick={() => navigate('/search')} className="text-primary text-sm font-medium hover:underline flex items-center gap-1 mx-auto">
                  <span className="material-symbols-outlined text-sm">search</span>
                  Go to Search
                </button>
              </div>
            </div>
          ) : (
            <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
              {COLUMNS.map((col) => (
                <KanbanColumn
                  key={col.status}
                  status={col.status}
                  label={col.label}
                  coas={coas.filter((c) => c.status === col.status)}
                  selectedId={selectedCoa?.id ?? null}
                  onSelect={setSelectedCoa}
                />
              ))}
            </DndContext>
          )}
        </div>
      </section>

      {/* Right Detail Panel — only visible when a COA is selected */}
      {selectedCoa && (
      <aside className="w-[480px] bg-surface-container-low border-l border-outline-variant/15 flex flex-col overflow-hidden">
        <div className="p-6 border-b border-outline-variant/10 flex items-center justify-between gap-2">
          <h2 className="text-sm font-headline font-bold uppercase tracking-widest text-primary">
            Detail View: {selectedCoa.id.slice(0, 8).toUpperCase()}
          </h2>
          <div className="flex items-center gap-1">
            <button
              onClick={editing ? handleCancelEdit : handleStartEdit}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider transition-colors ${
                editing
                  ? 'bg-surface-container-high text-on-surface'
                  : 'text-outline hover:text-on-surface hover:bg-surface-container-high/60'
              }`}
            >
              <span className="material-symbols-outlined text-sm">
                {editing ? 'close' : 'edit'}
              </span>
              {editing ? 'Cancel' : 'Edit'}
            </button>
            <button onClick={() => setSelectedCoa(null)} className="text-outline hover:text-on-surface transition-colors ml-1">
              <span className="material-symbols-outlined">close</span>
            </button>
          </div>
        </div>

        {selectedCoa && editing && editForm && (
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            <div>
              <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
                Name
              </label>
              <input
                type="text"
                value={editForm.name}
                onChange={(e) => setEditForm((f) => (f ? { ...f, name: e.target.value } : f))}
                className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-sm text-on-surface placeholder:text-outline focus:outline-none focus:border-primary/50"
              />
            </div>

            <div>
              <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
                Description
              </label>
              <textarea
                value={editForm.description}
                onChange={(e) => setEditForm((f) => (f ? { ...f, description: e.target.value } : f))}
                rows={4}
                className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-sm text-on-surface placeholder:text-outline focus:outline-none focus:border-primary/50 resize-none"
              />
            </div>

            <div>
              <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
                Action Type
              </label>
              <select
                value={editForm.action_type}
                onChange={(e) => setEditForm((f) => (f ? { ...f, action_type: e.target.value } : f))}
                className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-sm text-on-surface focus:outline-none focus:border-primary/50"
              >
                {ACTION_TYPE_OPTIONS.map((opt) => (
                  <option key={opt} value={opt}>
                    {opt.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
                Confidence (0.00 - 1.00)
              </label>
              <input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={editForm.confidence}
                onChange={(e) => setEditForm((f) => (f ? { ...f, confidence: e.target.value } : f))}
                placeholder="e.g. 0.75"
                className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-sm text-on-surface placeholder:text-outline focus:outline-none focus:border-primary/50"
              />
            </div>

            <div>
              <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
                Target Entities (comma-separated)
              </label>
              <input
                type="text"
                value={editForm.target_entities}
                onChange={(e) => setEditForm((f) => (f ? { ...f, target_entities: e.target.value } : f))}
                placeholder="e.g. CNOOC, Huawei, SMIC"
                className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-sm text-on-surface placeholder:text-outline focus:outline-none focus:border-primary/50"
              />
            </div>

            <div>
              <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
                Recommendations (one per line)
              </label>
              <textarea
                value={editForm.recommendations}
                onChange={(e) => setEditForm((f) => (f ? { ...f, recommendations: e.target.value } : f))}
                rows={4}
                className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-sm text-on-surface placeholder:text-outline focus:outline-none focus:border-primary/50 resize-none"
              />
            </div>

            <div>
              <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
                Expected Effects (one per line)
              </label>
              <textarea
                value={editForm.expected_effects}
                onChange={(e) => setEditForm((f) => (f ? { ...f, expected_effects: e.target.value } : f))}
                rows={4}
                className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-sm text-on-surface placeholder:text-outline focus:outline-none focus:border-primary/50 resize-none"
              />
            </div>

            <div>
              <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-error block mb-1.5">
                Friendly Fire (JSON array)
              </label>
              <textarea
                value={editForm.friendly_fire}
                onChange={(e) => setEditForm((f) => (f ? { ...f, friendly_fire: e.target.value } : f))}
                rows={6}
                spellCheck={false}
                className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-xs font-mono text-on-surface placeholder:text-outline focus:outline-none focus:border-primary/50 resize-none"
              />
            </div>

            {editError && (
              <div className="bg-error-container/20 border border-error/30 rounded-lg px-3 py-2 text-xs text-error">
                {editError}
              </div>
            )}

            <div className="flex gap-2 pt-2">
              <button
                onClick={handleSaveEdit}
                disabled={savingEdit}
                className="flex-1 bg-primary-container text-on-primary-container px-4 py-2.5 rounded-lg text-sm font-bold hover:brightness-110 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
              >
                <span className="material-symbols-outlined text-base">
                  {savingEdit ? 'progress_activity' : 'save'}
                </span>
                {savingEdit ? 'Saving...' : 'Save Changes'}
              </button>
              <button
                onClick={handleCancelEdit}
                disabled={savingEdit}
                className="px-4 py-2.5 rounded-lg text-sm font-bold border border-outline-variant/30 text-on-surface hover:bg-surface-container-high transition-all disabled:opacity-50"
              >
                Cancel
              </button>
            </div>

            <FollowUpBar contextType="coa" context={selectedCoa} />
          </div>
        )}

        {selectedCoa && !editing && (
          <div className="flex-1 overflow-y-auto p-6 space-y-5">
            {/* Name & Status */}
            <div>
              <h3 className="text-lg font-headline font-bold text-on-surface">
                {selectedCoa.name}
              </h3>
              <div className="flex items-center gap-2 mt-2">
                <span
                  className={`text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 rounded-full ${
                    STATUS_COLORS[selectedCoa.status]
                  }`}
                >
                  {statusLabel(selectedCoa.status)}
                </span>
                {selectedCoa.action_type && (
                  <span className="text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 rounded-full bg-surface-container-high text-on-surface-variant">
                    {selectedCoa.action_type}
                  </span>
                )}
                <span className={`text-xs font-bold ${confidenceDisplay(selectedCoa.confidence).cls}`}>
                  {confidenceDisplay(selectedCoa.confidence).label}
                </span>
              </div>
            </div>

            {/* Description */}
            {selectedCoa.description && (
              <div>
                <p className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline mb-1.5">
                  Description
                </p>
                <p className="text-sm text-on-surface-variant leading-relaxed">
                  {selectedCoa.description}
                </p>
              </div>
            )}

            {/* Rationale — analyst-grade "why" with [N] citations */}
            {selectedCoa.rationale && (
              <div>
                <p className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-tertiary mb-1.5">
                  Why This Action — Rationale
                </p>
                <p className="text-sm text-on-surface-variant leading-relaxed">
                  {renderWithCites(selectedCoa.rationale)}
                </p>
              </div>
            )}

            {/* Target Entities */}
            {selectedCoa.target_entities.length > 0 && (
              <div>
                <p className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline mb-1.5">
                  Target Entities
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {selectedCoa.target_entities.map((entity, i) => (
                    <span
                      key={i}
                      className="text-xs px-2.5 py-1 rounded-full bg-surface-container-high text-on-surface-variant font-mono"
                    >
                      {entity}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Recommendations */}
            {selectedCoa.recommendations.length > 0 && (
              <div>
                <p className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline mb-1.5">
                  Recommendations
                </p>
                <ol className="list-decimal list-inside space-y-1">
                  {selectedCoa.recommendations.map((rec, i) => (
                    <li
                      key={i}
                      className="text-sm text-on-surface-variant leading-relaxed"
                    >
                      {renderWithCites(rec)}
                    </li>
                  ))}
                </ol>
              </div>
            )}

            {/* Friendly Fire */}
            {selectedCoa.friendly_fire.length > 0 && (
              <div>
                <p className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-error mb-1.5">
                  Friendly Fire Risks
                </p>
                <div className="space-y-2">
                  {selectedCoa.friendly_fire.map((ff, i) => (
                    <div
                      key={i}
                      className="bg-error-container/20 border border-error/20 rounded-lg p-3"
                    >
                      {Object.entries(ff).map(([key, value]) => {
                        // cite_ids renders as inline footnote anchors; everything
                        // else passes through renderWithCites so embedded [N]
                        // markers in `because`/`impact` strings become clickable.
                        if (key === 'cite_ids' && Array.isArray(value)) {
                          return (
                            <p key={key} className="text-xs text-on-surface-variant">
                              <span className="font-bold text-error capitalize">Sources:</span>{' '}
                              {(value as number[]).map((n, j) => (
                                <span key={j}>
                                  {j > 0 && ' '}
                                  <a href={`#coa-src-${n}`} className="briefing-cite">[{n}]</a>
                                </span>
                              ))}
                            </p>
                          )
                        }
                        return (
                          <p key={key} className="text-xs text-on-surface-variant">
                            <span className="font-bold text-error capitalize">
                              {key.replace(/_/g, ' ')}:
                            </span>{' '}
                            {typeof value === 'string' ? renderWithCites(value) : String(value)}
                          </p>
                        )
                      })}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Expected Effects */}
            {selectedCoa.expected_effects.length > 0 && (
              <div>
                <p className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline mb-1.5">
                  Expected Effects
                </p>
                <ul className="space-y-1">
                  {selectedCoa.expected_effects.map((effect, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm text-on-surface-variant">
                      <span className="material-symbols-outlined text-sm text-primary mt-0.5">
                        arrow_right_alt
                      </span>
                      <span>{renderWithCites(effect)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Sources — bibliography for [N] markers above */}
            {selectedCoa.sources && selectedCoa.sources.length > 0 && (
              <div>
                <p className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline mb-1.5">
                  Sources
                </p>
                <ol className="space-y-1.5">
                  {selectedCoa.sources.map((s, i) => {
                    const n = i + 1
                    return (
                      <li
                        key={i}
                        id={`coa-src-${n}`}
                        className="text-xs text-on-surface-variant leading-relaxed scroll-mt-20 target:bg-tertiary/15 target:transition-colors target:duration-1000"
                      >
                        <span className="font-bold text-on-surface mr-1">[{n}]</span>
                        <span className="font-semibold">{s.name}</span>
                        {s.description && <span> — {s.description}</span>}
                        {s.url && (
                          <>
                            <br />
                            <a
                              href={s.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-primary hover:underline ml-4"
                            >
                              {s.url}
                            </a>
                          </>
                        )}
                        {s.record_url && (
                          <>
                            <br />
                            <a
                              href={s.record_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-primary hover:underline ml-4"
                            >
                              Record: {s.record_url}
                            </a>
                          </>
                        )}
                      </li>
                    )
                  })}
                </ol>
              </div>
            )}

            {/* Metadata */}
            <div className="text-[10px] text-outline space-y-0.5 pt-2 border-t border-outline-variant/10">
              <p>Created: {new Date(selectedCoa.created_at).toLocaleString()}</p>
              <p>Updated: {new Date(selectedCoa.updated_at).toLocaleString()}</p>
              {selectedCoa.source_analysis_id && (
                <p className="font-mono">Source: {selectedCoa.source_analysis_id}</p>
              )}
            </div>

            {/* Action Buttons */}
            <div className="flex flex-col gap-2 pt-2">
              <div className="flex gap-2">
                {STATUS_NEXT[selectedCoa.status] && (
                  <button
                    onClick={() => handleAdvanceStatus(selectedCoa)}
                    className="flex-1 bg-primary-container text-on-primary-container px-4 py-2.5 rounded-lg text-sm font-bold hover:brightness-110 transition-all flex items-center justify-center gap-2"
                  >
                    <span className="material-symbols-outlined text-base">
                      arrow_forward
                    </span>
                    {selectedCoa.status === 'draft'
                      ? 'Submit for Review'
                      : selectedCoa.status === 'under_review'
                        ? 'Approve for Execution'
                        : selectedCoa.status === 'approved'
                          ? 'Begin Execution'
                          : 'Mark Assessed'}
                  </button>
                )}
                <button
                  onClick={() => handleDelete(selectedCoa.id)}
                  className="px-4 py-2.5 rounded-lg text-sm font-bold border border-error/30 text-error hover:bg-error-container/20 transition-all flex items-center gap-2"
                >
                  <span className="material-symbols-outlined text-base">
                    delete
                  </span>
                  Delete
                </button>
              </div>
              <button
                onClick={() => handleGenerateBriefing(selectedCoa.id)}
                disabled={generatingBrief}
                className="w-full bg-secondary-container text-on-secondary-container px-4 py-2.5 rounded-lg text-sm font-bold hover:brightness-110 transition-all flex items-center justify-center gap-2 disabled:opacity-50"
              >
                <span className="material-symbols-outlined text-base">
                  {generatingBrief ? 'progress_activity' : 'present_to_all'}
                </span>
                {generatingBrief ? 'Generating Briefing...' : 'Generate Briefing'}
              </button>
              <button
                onClick={() => navigate('/briefings')}
                className="w-full text-primary text-xs font-medium hover:underline flex items-center justify-center gap-1 py-1"
              >
                <span className="material-symbols-outlined text-sm">open_in_new</span>
                View All Briefings
              </button>
            </div>

            <FollowUpBar contextType="coa" context={selectedCoa} />
          </div>
        )}
      </aside>
      )}

      {/* Generate COA Modal */}
      <Modal
        open={showGenerateModal}
        onClose={() => setShowGenerateModal(false)}
        title="Generate COA Options"
      >
        <div className="space-y-4">
          <div>
            <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
              Strategic Objective
            </label>
            <textarea
              value={generateObjective}
              onChange={(e) => setGenerateObjective(e.target.value)}
              placeholder="Describe the strategic objective for COA generation..."
              rows={4}
              className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-sm text-on-surface placeholder:text-outline focus:outline-none focus:border-primary/50 resize-none"
            />
          </div>

          <button
            onClick={handleGenerate}
            disabled={generating || !generateObjective.trim()}
            className="w-full bg-primary-container text-on-primary-container px-4 py-2.5 rounded-lg text-sm font-bold hover:brightness-110 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {generating ? (
              <>
                <span className="material-symbols-outlined text-base animate-spin">
                  progress_activity
                </span>
                Generating...
              </>
            ) : (
              <>
                <span className="material-symbols-outlined text-base">
                  bolt
                </span>
                Generate Options
              </>
            )}
          </button>

          {generatedOptions.length > 0 && (
            <div className="space-y-3 pt-2 border-t border-outline-variant/10">
              <p className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline">
                Generated Options ({generatedOptions.length})
              </p>
              {generatedOptions.map((opt, i) => (
                <div
                  key={i}
                  className="bg-surface-container-lowest border border-outline-variant/15 rounded-lg p-4 space-y-2"
                >
                  <p className="text-sm font-bold text-on-surface">
                    {opt.name || `Option ${i + 1}`}
                  </p>
                  {opt.description && (
                    <p className="text-xs text-on-surface-variant leading-relaxed">
                      {opt.description}
                    </p>
                  )}
                  {opt.action_type && (
                    <span className="inline-block text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-surface-container-high text-on-surface-variant">
                      {opt.action_type}
                    </span>
                  )}
                  {opt.rationale && (
                    <div className="border-l-2 border-tertiary/50 pl-3 py-1">
                      <p className="text-[10px] font-headline font-bold uppercase tracking-wider text-tertiary mb-1">
                        Why this action
                      </p>
                      <p className="text-xs text-on-surface-variant leading-relaxed">
                        {renderWithCites(opt.rationale)}
                      </p>
                    </div>
                  )}
                  {opt.expected_effects && opt.expected_effects.length > 0 && (
                    <ul className="space-y-0.5">
                      {opt.expected_effects.map((e, j) => (
                        <li
                          key={j}
                          className="text-xs text-on-surface-variant flex items-start gap-1"
                        >
                          <span className="text-primary">-</span> <span>{renderWithCites(e)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  <button
                    onClick={() => acceptOption(opt)}
                    className="mt-1 bg-primary-container text-on-primary-container px-3 py-1.5 rounded-lg text-xs font-bold hover:brightness-110 transition-all flex items-center gap-1.5"
                  >
                    <span className="material-symbols-outlined text-sm">
                      check
                    </span>
                    Accept
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </Modal>

      {/* Create COA Modal */}
      <Modal
        open={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        title="Create New COA"
      >
        <div className="space-y-4">
          <div>
            <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
              Name *
            </label>
            <input
              type="text"
              value={createForm.name}
              onChange={(e) =>
                setCreateForm((f) => ({ ...f, name: e.target.value }))
              }
              placeholder="COA name"
              className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-sm text-on-surface placeholder:text-outline focus:outline-none focus:border-primary/50"
            />
          </div>

          <div>
            <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
              Description
            </label>
            <textarea
              value={createForm.description}
              onChange={(e) =>
                setCreateForm((f) => ({ ...f, description: e.target.value }))
              }
              placeholder="Describe the course of action..."
              rows={3}
              className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-sm text-on-surface placeholder:text-outline focus:outline-none focus:border-primary/50 resize-none"
            />
          </div>

          <div>
            <label className="text-[10px] font-headline font-bold uppercase tracking-[0.15em] text-outline block mb-1.5">
              Action Type
            </label>
            <select
              value={createForm.action_type}
              onChange={(e) =>
                setCreateForm((f) => ({ ...f, action_type: e.target.value }))
              }
              className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2.5 text-sm text-on-surface focus:outline-none focus:border-primary/50"
            >
              <option value="sanction">Sanction</option>
              <option value="tariff">Tariff</option>
              <option value="trade_restriction">Trade Restriction</option>
              <option value="asset_freeze">Asset Freeze</option>
              <option value="diplomatic">Diplomatic</option>
              <option value="cyber">Cyber</option>
              <option value="financial">Financial</option>
              <option value="other">Other</option>
            </select>
          </div>

          <button
            onClick={handleCreate}
            disabled={!createForm.name.trim()}
            className="w-full bg-primary-container text-on-primary-container px-4 py-2.5 rounded-lg text-sm font-bold hover:brightness-110 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            <span className="material-symbols-outlined text-base">add</span>
            Create COA
          </button>
        </div>
      </Modal>
    </div>
  )
}
