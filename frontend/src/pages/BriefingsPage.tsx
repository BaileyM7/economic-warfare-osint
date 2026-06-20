import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { marked } from 'marked'
import {
  fetchBriefings,
  createBriefing,
  generateBriefing,
  updateBriefing,
  deleteBriefing,
  fetchCOAs,
} from '../api'
import SelfServeBriefs from '../components/SelfServeBriefs'
import type { Briefing, COA } from '../types'

const TYPE_LABELS: Record<Briefing['type'], string> = {
  coa_brief: 'COA Brief',
  bda_report: 'BDA Report',
  situation_update: 'Situation Update',
  exercise_summary: 'Exercise Summary',
}

const TYPE_BADGE_CLASSES: Record<Briefing['type'], string> = {
  coa_brief: 'bg-primary-container text-on-primary-container',
  bda_report: 'bg-tertiary/15 text-tertiary',
  situation_update: 'bg-secondary-container text-on-secondary-container',
  exercise_summary: 'border border-outline-variant text-outline',
}

function statusDot(status: Briefing['status']) {
  switch (status) {
    case 'draft':
      return 'bg-outline-variant'
    case 'reviewing':
      return 'bg-tertiary'
    case 'finalized':
      return 'bg-secondary'
  }
}

function statusLabel(status: Briefing['status']) {
  switch (status) {
    case 'draft':
      return 'Draft'
    case 'reviewing':
      return 'Reviewing'
    case 'finalized':
      return 'Finalized'
  }
}

function nextStatus(status: Briefing['status']): Briefing['status'] | null {
  switch (status) {
    case 'draft':
      return 'reviewing'
    case 'reviewing':
      return 'finalized'
    case 'finalized':
      return null
  }
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  }).toUpperCase()
}

function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8).toUpperCase() : id.toUpperCase()
}

export default function BriefingsPage() {
  const navigate = useNavigate()
  const columns = ['Title', 'Type', 'Status', 'Actions']

  const [briefings, setBriefings] = useState<Briefing[]>([])
  const [selectedBriefing, setSelectedBriefing] = useState<Briefing | null>(null)
  const [showNewModal, setShowNewModal] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [coas, setCoas] = useState<COA[]>([])
  const [statusFilter, setStatusFilter] = useState<Briefing['status'] | 'all'>('all')
  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState('')

  // Modal form state
  const [formTitle, setFormTitle] = useState('')
  const [formType, setFormType] = useState<Briefing['type']>('coa_brief')
  const [formCoaId, setFormCoaId] = useState('')

  const loadBriefings = useCallback(async () => {
    try {
      const data = await fetchBriefings()
      setBriefings(data)
    } catch {
      // silently fail - empty list shown
    }
  }, [])

  const loadCOAs = useCallback(async () => {
    try {
      const data = await fetchCOAs()
      setCoas(data)
    } catch {
      // silently fail
    }
  }, [])

  useEffect(() => {
    loadBriefings()
    loadCOAs()
  }, [loadBriefings, loadCOAs])

  const handleSelectBriefing = (b: Briefing) => {
    setSelectedBriefing(b)
    setEditing(false)
    setEditText('')
  }

  const handleApprove = async () => {
    if (!selectedBriefing) return
    const next = nextStatus(selectedBriefing.status)
    if (!next) return
    try {
      const updated = await updateBriefing(selectedBriefing.id, { status: next })
      setSelectedBriefing(updated)
      setBriefings((prev) =>
        prev.map((b) => (b.id === updated.id ? updated : b)),
      )
    } catch {
      // fail silently
    }
  }

  const handleStartEdit = () => {
    if (!selectedBriefing) return
    setEditText(selectedBriefing.content_markdown || '')
    setEditing(true)
  }

  const handleCancelEdit = () => {
    setEditing(false)
    setEditText('')
  }

  const handleSaveEdit = async () => {
    if (!selectedBriefing) return
    try {
      const updated = await updateBriefing(selectedBriefing.id, { content_markdown: editText })
      setSelectedBriefing(updated)
      setBriefings((prev) =>
        prev.map((b) => (b.id === updated.id ? updated : b)),
      )
      setEditing(false)
      setEditText('')
    } catch {
      // fail silently
    }
  }

  const handleDeleteBriefing = async (b: Briefing) => {
    if (!window.confirm(`Delete briefing "${b.title}"?`)) return
    try {
      await deleteBriefing(b.id)
      setBriefings((prev) => prev.filter((x) => x.id !== b.id))
      if (selectedBriefing?.id === b.id) {
        setSelectedBriefing(null)
        setEditing(false)
        setEditText('')
      }
    } catch {
      // fail silently
    }
  }

  const [exporting, setExporting] = useState(false)

  const exportPDF = async (briefing: Briefing) => {
    setExporting(true)
    try {
      const { default: jsPDF } = await import('jspdf')
      const { default: html2canvas } = await import('html2canvas')

      const htmlContent = marked(briefing.content_markdown || '') as string

      const container = document.createElement('div')
      container.style.cssText = [
        'position:fixed',
        'left:-9999px',
        'top:0',
        'width:794px',                 // ~210mm at 96dpi
        'background:#fff',
        'color:#1c1f29',
        'font-family:Segoe UI,system-ui,sans-serif',
        'font-size:12px',
        'line-height:1.55',
        'padding:48px',
        'box-sizing:border-box',
      ].join(';')
      container.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:24px;padding-bottom:24px;border-bottom:2px solid rgba(0,0,0,0.1)">
          <div style="width:48px;height:48px;border:4px solid rgba(0,0,0,0.2);display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:20px">E</div>
          <div style="text-align:right">
            <div style="font-size:10px;font-weight:bold;letter-spacing:0.15em;text-transform:uppercase">Document No.</div>
            <div style="font-size:14px;font-family:monospace">${shortId(briefing.id)}</div>
            <div style="font-size:10px;color:rgba(0,0,0,0.5);margin-top:4px">${formatDate(briefing.created_at)}</div>
          </div>
        </div>
        <div style="font-size:20px;font-weight:900;text-transform:uppercase;letter-spacing:-0.02em;margin-bottom:24px">${briefing.title}</div>
        <div data-briefing-body>${htmlContent}</div>
        <div style="margin-top:36px;background:rgba(0,0,0,0.03);text-align:center;padding:12px">
          <div style="font-size:10px;font-weight:bold;letter-spacing:0.4em;text-transform:uppercase;color:rgba(0,0,0,0.4)">EMISSARY DEMO DOCUMENT</div>
        </div>
      `
      document.body.appendChild(container)

      try {
        const canvas = await html2canvas(container, { scale: 2, useCORS: true, backgroundColor: '#ffffff' })
        const imgWidth = 210
        const pxPerMm = canvas.width / imgWidth
        const pageHeightMm = 297
        const pageHeightPx = pageHeightMm * pxPerMm

        // PIXEL-BASED gap detection: scan the rasterized canvas from top
        // to bottom and find rows that are 100% background (white). Those
        // rows are guaranteed gaps between lines/blocks because if any text
        // ink were present it would darken at least one pixel in the row.
        // Cutting on a pure-white row is safe regardless of how html2canvas
        // positioned the lines vs our DOM measurements — the source of truth
        // is the canvas itself, not a DOM-side estimate that can drift.
        const ctxFull = canvas.getContext('2d')!
        const imgData = ctxFull.getImageData(0, 0, canvas.width, canvas.height)
        const data = imgData.data
        // emptyRow[y] = true means row y has no non-white pixels.
        const emptyRow = new Uint8Array(canvas.height)
        for (let y = 0; y < canvas.height; y++) {
          let hasInk = false
          // Sample every 4th pixel in the row to keep scan cost reasonable
          // (scale:2 + A4 width → ~1600 px wide → ~400 samples per row).
          for (let x = 0; x < canvas.width; x += 4) {
            const i = (y * canvas.width + x) * 4
            // RGB threshold: 250 catches pure white and near-white antialias
            // halos. If any sampled pixel is darker, the row contains ink.
            if (data[i] < 250 || data[i + 1] < 250 || data[i + 2] < 250) {
              hasInk = true
              break
            }
          }
          emptyRow[y] = hasInk ? 0 : 1
        }

        const pdf = new jsPDF({ unit: 'mm', format: 'a4', orientation: 'portrait' })
        let pageStart = 0
        let pageNum = 0
        while (pageStart < canvas.height) {
          const idealEnd = pageStart + pageHeightPx
          let pageEnd = idealEnd
          if (idealEnd < canvas.height) {
            // Walk backwards from idealEnd looking for a row that's empty
            // AND whose neighbours are also empty (we want a gap, not a
            // single isolated whitespace pixel inside antialiased text).
            // Require 2 consecutive empty rows = ~1px in PDF, indistinguishable
            // from natural inter-line whitespace.
            let cut = -1
            const minEnd = pageStart + Math.floor(pageHeightPx * 0.4)
            for (let y = Math.floor(idealEnd); y >= minEnd; y--) {
              if (emptyRow[y] && emptyRow[y - 1]) {
                cut = y
                break
              }
            }
            pageEnd = cut > 0 ? cut : Math.floor(idealEnd)
          } else {
            pageEnd = canvas.height
          }
          const sliceCanvas = document.createElement('canvas')
          sliceCanvas.width = canvas.width
          sliceCanvas.height = Math.max(1, pageEnd - pageStart)
          const ctx = sliceCanvas.getContext('2d')!
          ctx.fillStyle = '#ffffff'
          ctx.fillRect(0, 0, sliceCanvas.width, sliceCanvas.height)
          ctx.drawImage(canvas, 0, pageStart, canvas.width, pageEnd - pageStart, 0, 0, canvas.width, pageEnd - pageStart)
          if (pageNum > 0) pdf.addPage()
          const sliceHeightMm = (pageEnd - pageStart) / pxPerMm
          pdf.addImage(sliceCanvas.toDataURL('image/png'), 'PNG', 0, 0, imgWidth, sliceHeightMm)
          pageStart = pageEnd
          pageNum++
          if (pageNum > 30) break
        }

        pdf.save(`${briefing.title.replace(/\s+/g, '_')}.pdf`)
      } finally {
        document.body.removeChild(container)
      }
    } catch (err) {
      console.error('PDF export failed:', err)
    } finally {
      setExporting(false)
    }
  }

  const handleCreateEmpty = async () => {
    if (!formTitle.trim()) return
    try {
      const created = await createBriefing({ title: formTitle, type: formType })
      setBriefings((prev) => [created, ...prev])
      setSelectedBriefing(created)
      resetModal()
    } catch {
      // fail silently
    }
  }

  const handleGenerate = async () => {
    setGenerating(true)
    try {
      const created = await generateBriefing({
        coa_id: formCoaId || undefined,
        briefing_type: formType,
      })
      setBriefings((prev) => [created, ...prev])
      setSelectedBriefing(created)
      resetModal()
    } catch {
      // fail silently
    } finally {
      setGenerating(false)
    }
  }

  const resetModal = () => {
    setShowNewModal(false)
    setFormTitle('')
    setFormType('coa_brief')
    setFormCoaId('')
  }

  // Filtered view
  const filteredBriefings = statusFilter === 'all'
    ? briefings
    : briefings.filter((b) => b.status === statusFilter)

  // Analytics
  const pendingApprovals = briefings.filter((b) => b.status === 'reviewing').length
  const activeBdaCycles = briefings.filter(
    (b) => b.type === 'bda_report' && b.status !== 'finalized',
  ).length
  const finalizedBriefs = briefings.filter((b) => b.status === 'finalized').length
  const intelFeedHealth = briefings.length > 0
    ? Math.round((finalizedBriefs / briefings.length) * 100)
    : 0

  const approveLabel =
    selectedBriefing?.status === 'draft'
      ? 'SUBMIT FOR REVIEW'
      : selectedBriefing?.status === 'reviewing'
        ? 'APPROVE BRIEF'
        : 'FINALIZED'

  // Wrap inline citation markers like [1], [2] with anchor links that scroll
  // to the matching `[N] Source name` line in the Sources section. We tag the
  // first occurrence of "[N] " at the start of a line as the anchor target,
  // and every other occurrence (inside Findings, Risk, etc.) as a link to it.
  // This is the analyst-grade "click footnote → see source" behaviour.
  const linkifyCitations = (html: string): string => {
    let sourceAnchorsAdded = new Set<string>()
    return html
      .replace(/(<(?:p|li)[^>]*>\s*)\[(\d+)\]\s/g, (_m, prefix: string, n: string) => {
        // Source list entry: "<p>[N] ..." → add anchor target on first hit
        if (sourceAnchorsAdded.has(n)) return `${prefix}[${n}] `
        sourceAnchorsAdded.add(n)
        return `${prefix}<span id="src-${n}" class="briefing-source-target">[${n}]</span> `
      })
      .replace(/\[(\d+)\](?!\s*[A-Z][a-z])/g, (_m, n: string) =>
        `<a href="#src-${n}" class="briefing-cite" data-cite="${n}">[${n}]</a>`,
      )
  }
  const renderedMarkdown = selectedBriefing
    ? linkifyCitations(marked(selectedBriefing.content_markdown || '') as string)
    : ''

  return (
    <div className="p-8 max-w-7xl mx-auto grid grid-cols-12 gap-8">
      {/* Header */}
      <div className="col-span-12 flex justify-between items-end mb-4">
        <div>
          <p className="text-tertiary font-label text-[10px] uppercase tracking-widest mb-1">
            Intelligence Repository
          </p>
          <h1 className="text-3xl font-headline font-bold text-on-surface">
            Mission Briefings
          </h1>
        </div>
        <div className="flex gap-3">
          <select
            className="bg-surface-container border border-outline-variant/30 text-on-surface px-4 py-2 rounded-lg text-xs font-medium hover:bg-surface-bright transition-all"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as Briefing['status'] | 'all')}
          >
            <option value="all">All Status</option>
            <option value="draft">Draft</option>
            <option value="reviewing">Reviewing</option>
            <option value="finalized">Finalized</option>
          </select>
          <button
            className="bg-primary-container text-on-primary-container px-4 py-2 rounded-lg flex items-center gap-2 text-xs font-bold hover:brightness-110 transition-all"
            onClick={() => setShowNewModal(true)}
          >
            <span className="material-symbols-outlined text-sm">add</span>
            NEW BRIEF
          </button>
        </div>
      </div>

      {/* Briefing List Table */}
      <div className="col-span-12 lg:col-span-7 bg-surface-container-low rounded-xl overflow-hidden">
        <div className="p-4 border-b border-outline-variant/10 flex justify-between items-center bg-surface-container-lowest/50">
          <span className="text-[10px] font-label uppercase tracking-widest text-outline">
            Active Briefings
          </span>
          <span className="text-[10px] text-primary">
            Showing {filteredBriefings.length} of {briefings.length} Records
          </span>
        </div>
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-surface-container-lowest/30">
              {columns.map((col) => (
                <th
                  key={col}
                  className="px-6 py-3 text-[10px] font-label uppercase tracking-widest text-outline"
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filteredBriefings.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-6 py-12 text-center">
                  <span className="material-symbols-outlined text-3xl text-outline block mb-2">
                    present_to_all
                  </span>
                  <p className="text-sm text-outline">No briefings generated yet</p>
                  <p className="text-[10px] text-outline mt-1 mb-3">
                    Create a COA first, then generate a briefing from it — or click NEW BRIEF above.
                  </p>
                  <button onClick={() => navigate('/coa')} className="text-primary text-xs font-medium hover:underline inline-flex items-center gap-1">
                    <span className="material-symbols-outlined text-xs">account_tree</span>
                    Go to COA Workspace
                  </button>
                </td>
              </tr>
            ) : (
              filteredBriefings.map((b) => {
                const isSelected = selectedBriefing?.id === b.id
                return (
                  <tr
                    key={b.id}
                    onClick={() => handleSelectBriefing(b)}
                    className={`cursor-pointer transition-colors border-l-2 ${
                      isSelected
                        ? 'bg-primary-container/10 border-l-primary'
                        : 'border-l-transparent hover:bg-surface-container-lowest/40'
                    }`}
                  >
                    <td className="px-6 py-3">
                      <span className="text-sm font-medium text-on-surface">{b.title}</span>
                    </td>
                    <td className="px-6 py-3">
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-[10px] font-label uppercase tracking-wider ${TYPE_BADGE_CLASSES[b.type]}`}
                      >
                        {TYPE_LABELS[b.type]}
                      </span>
                    </td>
                    <td className="px-6 py-3">
                      <span className="flex items-center gap-1.5">
                        <span className={`w-2 h-2 rounded-full ${statusDot(b.status)}`} />
                        <span className="text-xs text-outline">{statusLabel(b.status)}</span>
                      </span>
                    </td>
                    <td className="px-6 py-3">
                      <div className="flex items-center gap-2">
                        <button
                          className="text-outline hover:text-on-surface transition-colors"
                          onClick={(e) => {
                            e.stopPropagation()
                            exportPDF(b)
                          }}
                          title="Download PDF"
                        >
                          <span className="material-symbols-outlined text-lg">download</span>
                        </button>
                        <button
                          className="text-outline hover:text-error transition-colors"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleDeleteBriefing(b)
                          }}
                          title="Delete briefing"
                        >
                          <span className="material-symbols-outlined text-lg">delete</span>
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Document Preview Card */}
      <div className="col-span-12 lg:col-span-5 briefing-print-wrapper">
        <div className="bg-white text-[#1c1f29] rounded-sm shadow-2xl relative min-h-[500px] overflow-hidden flex flex-col briefing-print-target">
          {/* Document Header */}
          <div className="p-8 border-b-2 border-black/10">
            <div className="flex justify-between items-start mb-6">
              <div className="w-12 h-12 border-4 border-black/20 flex items-center justify-center font-bold text-xl">
                E
              </div>
              <div className="text-right">
                <div className="text-[10px] font-bold tracking-widest uppercase">Document No.</div>
                <div className="text-sm font-mono">
                  {selectedBriefing ? shortId(selectedBriefing.id) : '---'}
                </div>
                {selectedBriefing && (
                  <div className="text-[10px] text-black/50 mt-1">
                    {formatDate(selectedBriefing.created_at)}
                  </div>
                )}
                {selectedBriefing?.reference_id && (
                  <button
                    className="text-[10px] text-blue-600 hover:underline mt-1 cursor-pointer no-print"
                    onClick={() => navigate('/coa')}
                  >
                    View Source COA &rarr;
                  </button>
                )}
              </div>
            </div>
            <div className="flex justify-between items-end">
              <h2 className="text-xl font-headline font-black uppercase leading-none tracking-tight">
                {selectedBriefing ? (
                  <span className="text-black">{selectedBriefing.title}</span>
                ) : (
                  <span className="text-black/30">Select a briefing to preview</span>
                )}
              </h2>
              {selectedBriefing && (
                <button
                  className={`flex items-center gap-1 px-2.5 py-1 rounded text-[10px] font-bold uppercase tracking-wider transition-colors no-print ${
                    editing
                      ? 'bg-black/10 text-black/70'
                      : 'text-black/40 hover:text-black/70 hover:bg-black/5'
                  }`}
                  onClick={editing ? handleCancelEdit : handleStartEdit}
                >
                  <span className="material-symbols-outlined text-sm">
                    {editing ? 'visibility' : 'edit'}
                  </span>
                  {editing ? 'View' : 'Edit'}
                </button>
              )}
            </div>
          </div>

          {/* Document Body */}
          {selectedBriefing ? (
            editing ? (
              <div className="p-8 flex-1 flex flex-col gap-3">
                <textarea
                  className="flex-1 w-full bg-white text-[#1c1f29] border border-black/10 rounded p-4 text-sm font-mono leading-relaxed resize-none focus:outline-none focus:border-black/30 transition-colors"
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  style={{ minHeight: '300px' }}
                />
                <div className="flex gap-2 justify-end">
                  <button
                    className="px-3 py-1.5 rounded text-xs font-medium text-[#1c1f29]/70 border border-black/10 hover:bg-black/5 transition-colors"
                    onClick={handleCancelEdit}
                  >
                    Cancel
                  </button>
                  <button
                    className="px-3 py-1.5 rounded text-xs font-bold bg-accent text-white hover:bg-accent-hover transition-colors"
                    onClick={handleSaveEdit}
                  >
                    Save
                  </button>
                </div>
              </div>
            ) : (
              <div
                className="p-8 flex-1 prose prose-sm max-w-none text-[#1c1f29]
                  prose-headings:text-[#1c1f29] prose-headings:font-headline
                  prose-p:text-[#1c1f29]/85 prose-p:leading-relaxed
                  prose-strong:text-[#1c1f29]
                  prose-li:text-[#1c1f29]/85
                  prose-a:text-blue-700
                  prose-code:text-[#1c1f29] prose-code:bg-black/5 prose-code:px-1 prose-code:rounded
                  prose-hr:border-black/10"
                dangerouslySetInnerHTML={{ __html: renderedMarkdown }}
              />
            )
          ) : (
            <div className="p-8 flex-1 flex items-center justify-center">
              <p className="text-sm text-black/30 italic">
                Document content will appear here when a briefing is selected.
              </p>
            </div>
          )}

          {/* Document Footer */}
          <div className="p-4 bg-black/5 text-center">
            <div className="text-[10px] font-bold tracking-[0.4em] uppercase text-black/40">
              EMISSARY DEMO DOCUMENT
            </div>
          </div>
        </div>

        {/* Action buttons below card */}
        {selectedBriefing && (
          <div className="flex gap-3 mt-4 no-print">
            <button
              className="flex-1 bg-surface-container border border-outline-variant/30 text-on-surface px-4 py-2.5 rounded-lg flex items-center justify-center gap-2 text-xs font-medium hover:bg-surface-bright transition-all disabled:opacity-50"
              onClick={() => exportPDF(selectedBriefing)}
              disabled={exporting}
            >
              <span className="material-symbols-outlined text-sm">{exporting ? 'progress_activity' : 'picture_as_pdf'}</span>
              {exporting ? 'EXPORTING...' : 'EXPORT PDF'}
            </button>
            <button
              className={`flex-1 px-4 py-2.5 rounded-lg flex items-center justify-center gap-2 text-xs font-bold transition-all ${
                selectedBriefing.status === 'finalized'
                  ? 'bg-secondary-container text-on-secondary-container opacity-60 cursor-not-allowed'
                  : 'bg-primary-container text-on-primary-container hover:brightness-110'
              }`}
              disabled={selectedBriefing.status === 'finalized'}
              onClick={handleApprove}
            >
              <span className="material-symbols-outlined text-sm">
                {selectedBriefing.status === 'finalized' ? 'check_circle' : 'approval'}
              </span>
              {approveLabel}
            </button>
          </div>
        )}
      </div>

      {/* Self-serve briefs — subscribe + send-now */}
      <div className="col-span-12 lg:col-span-5">
        <SelfServeBriefs />
      </div>

      {/* Analytics Grid */}
      <div className="col-span-12 grid grid-cols-4 gap-4 mt-4">
        {[
          {
            label: 'Pending Approvals',
            value: pendingApprovals,
            accent: 'border-primary',
            icon: 'pending_actions',
          },
          {
            label: 'Active BDA Cycles',
            value: activeBdaCycles,
            accent: 'border-tertiary',
            icon: 'target',
          },
          {
            label: 'Finalized Briefs',
            value: finalizedBriefs,
            accent: 'border-secondary',
            icon: 'verified',
          },
          {
            label: 'Intel Feed Health',
            value: `${intelFeedHealth}%`,
            accent: 'border-outline',
            icon: 'monitor_heart',
          },
        ].map((card) => (
          <div
            key={card.label}
            className={`bg-surface-container-low p-4 rounded-lg flex flex-col justify-between h-32 border-l-2 ${card.accent}`}
          >
            <div className="flex justify-between items-start">
              <span className="text-[10px] font-label uppercase tracking-widest text-outline">
                {card.label}
              </span>
              <span className="material-symbols-outlined text-lg text-outline">
                {card.icon}
              </span>
            </div>
            <span className="text-3xl font-headline font-bold text-on-surface">{card.value}</span>
          </div>
        ))}
      </div>

      {/* New Brief Modal */}
      {showNewModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={(e) => {
            if (e.target === e.currentTarget) resetModal()
          }}
        >
          <div className="bg-surface-container-low rounded-2xl shadow-2xl w-full max-w-md p-0 overflow-hidden">
            {/* Modal header */}
            <div className="px-6 py-5 border-b border-outline-variant/10 flex justify-between items-center">
              <div>
                <p className="text-[10px] font-label uppercase tracking-widest text-outline mb-1">
                  Intelligence Repository
                </p>
                <h2 className="text-lg font-headline font-bold text-on-surface">
                  Create New Briefing
                </h2>
              </div>
              <button
                className="text-outline hover:text-on-surface transition-colors"
                onClick={resetModal}
              >
                <span className="material-symbols-outlined">close</span>
              </button>
            </div>

            {/* Modal body */}
            <div className="px-6 py-5 space-y-4">
              {/* Title */}
              <div>
                <label className="block text-[10px] font-label uppercase tracking-widest text-outline mb-1.5">
                  Title
                </label>
                <input
                  type="text"
                  value={formTitle}
                  onChange={(e) => setFormTitle(e.target.value)}
                  placeholder="Enter briefing title..."
                  className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2 text-sm text-on-surface placeholder:text-outline focus:outline-none focus:border-primary/50 transition-colors"
                />
              </div>

              {/* Type */}
              <div>
                <label className="block text-[10px] font-label uppercase tracking-widest text-outline mb-1.5">
                  Type
                </label>
                <select
                  value={formType}
                  onChange={(e) => setFormType(e.target.value as Briefing['type'])}
                  className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2 text-sm text-on-surface focus:outline-none focus:border-primary/50 transition-colors"
                >
                  <option value="coa_brief">COA Brief</option>
                  <option value="bda_report">BDA Report</option>
                  <option value="situation_update">Situation Update</option>
                  <option value="exercise_summary">Exercise Summary</option>
                </select>
              </div>

              {/* Source COA */}
              <div>
                <label className="block text-[10px] font-label uppercase tracking-widest text-outline mb-1.5">
                  Source (Optional)
                </label>
                <select
                  value={formCoaId}
                  onChange={(e) => setFormCoaId(e.target.value)}
                  className="w-full bg-surface-container-lowest border border-outline-variant/20 rounded-lg px-3 py-2 text-sm text-on-surface focus:outline-none focus:border-primary/50 transition-colors"
                >
                  <option value="">-- No source --</option>
                  {coas.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Modal footer */}
            <div className="px-6 py-4 border-t border-outline-variant/10 flex gap-3 justify-end">
              <button
                className="bg-surface-container border border-outline-variant/30 text-on-surface px-4 py-2 rounded-lg text-xs font-medium hover:bg-surface-bright transition-all"
                onClick={resetModal}
              >
                Cancel
              </button>
              <button
                className="bg-surface-container border border-outline-variant/30 text-on-surface px-4 py-2 rounded-lg text-xs font-medium hover:bg-surface-bright transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                disabled={!formTitle.trim()}
                onClick={handleCreateEmpty}
              >
                Create Empty
              </button>
              <button
                className="bg-primary-container text-on-primary-container px-4 py-2 rounded-lg text-xs font-bold hover:brightness-110 transition-all disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2"
                disabled={generating}
                onClick={handleGenerate}
              >
                {generating && (
                  <span className="material-symbols-outlined text-sm animate-spin">progress_activity</span>
                )}
                {generating ? 'Generating...' : 'Generate from Source'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
