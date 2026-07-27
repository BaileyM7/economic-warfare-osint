import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import type {
  OrchestratorEvent,
  OrchestratorDomain,
  OrchestratorPlanStep,
  OrchestratorAgentDetail,
} from '../types'

/**
 * SwarmPanel — renders the orchestrator's live agent swarm from the Phase 3
 * `events[]` stream: the decomposition plan as a roster, then each tool call
 * lighting up (pending → running → done/error), grouped by step and coloured
 * by domain. Pure presentation of the append-only event list.
 *
 * Brand style: navy blocks, sharp corners, hairline dividers, the bounded
 * accent palette for domain encoding, mono labels. No shadows, no rounding.
 */

interface Props {
  events: OrchestratorEvent[]
  loading: boolean
}

const DOMAIN: Record<OrchestratorDomain, { label: string; color: string }> = {
  sanctions: { label: 'Sanctions', color: '#ff5a58' },
  geopolitical: { label: 'Geopolitical', color: '#d23c3a' },
  corporate: { label: 'Corporate', color: '#5b9bd4' },
  economic: { label: 'Economic', color: '#0092ff' },
  trade: { label: 'Trade', color: '#a9d8fb' },
  market: { label: 'Market', color: '#efb16a' },
  sayari: { label: 'Sayari', color: '#efb16a' },
  news: { label: 'News', color: 'rgba(255,255,255,0.6)' },
  unknown: { label: 'Signal', color: 'rgba(255,255,255,0.55)' },
}

const STAGES = ['decompose', 'execute', 'synthesize'] as const

interface AgentState {
  step: number
  name: string
  domain: OrchestratorDomain
  task?: string
  status: 'pending' | 'running' | 'done' | 'error'
  summary?: string
  ms?: number
  detail?: OrchestratorAgentDetail
}

interface FeedItem {
  key: string
  name: string
  domain: OrchestratorDomain
  items: string[]
  sources?: string[]
  error?: string
  status: 'done' | 'error'
}

function agentKey(step: number, name: string) {
  return `${step}::${name}`
}

export default function SwarmPanel({ events, loading }: Props) {
  const { agentsByStep, stageStatus, doneCount, totalCount, feed } = useMemo(() => {
    const planEvent = events.find((e) => e.type === 'plan')
    const plan: OrchestratorPlanStep[] = planEvent?.type === 'plan' ? planEvent.steps : []

    // Merge tool running/done|error into a single state per (step, name).
    const agents = new Map<string, AgentState>()
    for (const e of events) {
      if (e.type !== 'tool') continue
      const key = agentKey(e.step, e.name)
      const prev = agents.get(key)
      if (e.status === 'running') {
        agents.set(key, {
          step: e.step,
          name: e.name,
          domain: e.domain,
          task: e.task ?? prev?.task,
          status: 'running',
        })
      } else {
        agents.set(key, {
          step: e.step,
          name: e.name,
          domain: e.domain,
          task: prev?.task,
          status: e.status === 'error' ? 'error' : 'done',
          summary: e.summary,
          ms: e.ms,
          detail: e.detail,
        })
      }
    }

    // Seed pending agents from the plan roster so the whole swarm shows upfront.
    for (const s of plan) {
      for (const name of s.tools) {
        const key = agentKey(s.step, name)
        if (!agents.has(key)) {
          agents.set(key, { step: s.step, name, domain: 'unknown', status: 'pending' })
        }
      }
    }

    // Group by step, preserving plan order then any extras.
    const stepNums = Array.from(new Set([...plan.map((s) => s.step), ...[...agents.values()].map((a) => a.step)])).sort(
      (a, b) => a - b,
    )
    const agentsByStep = stepNums.map((step) => ({
      step,
      description: plan.find((s) => s.step === step)?.description ?? '',
      agents: [...agents.values()].filter((a) => a.step === step),
    }))

    // Phase stepper state — exactly ONE stage is "active" at a time: the
    // furthest-along phase that has started. Earlier phases show "done", later
    // ones "idle". Derived purely from the ORDER of phase `start` events (the
    // backend emits start-per-phase + a single final `complete`, no per-phase
    // `done`), so this also works on cached/warmed replays without re-warming.
    const complete = events.some((e) => e.type === 'phase' && e.name === 'complete')
    let currentIdx = -1
    STAGES.forEach((stage, i) => {
      if (events.some((e) => e.type === 'phase' && e.name === stage && e.status === 'start')) {
        currentIdx = i
      }
    })
    const stageStatus: Record<string, 'idle' | 'active' | 'done'> = {}
    STAGES.forEach((stage, i) => {
      // Respect an explicit per-phase `done` if the backend ever emits one.
      const explicitlyDone = events.some(
        (e) => e.type === 'phase' && e.name === stage && e.status === 'done',
      )
      if (complete || explicitlyDone || i < currentIdx) {
        stageStatus[stage] = 'done'
      } else if (i === currentIdx) {
        stageStatus[stage] = 'active'
      } else {
        stageStatus[stage] = 'idle'
      }
    })

    const all = [...agents.values()]
    const doneCount = all.filter((a) => a.status === 'done' || a.status === 'error').length
    const totalCount = all.filter((a) => a.status !== 'pending').length || all.length

    // Live intelligence feed: each agent's findings, in the order they completed.
    const feed: FeedItem[] = []
    for (const e of events) {
      if (e.type !== 'tool' || (e.status !== 'done' && e.status !== 'error')) continue
      const d = e.detail
      if (!d || (!(d.items && d.items.length) && !d.error)) continue
      feed.push({
        key: `${e.step}:${e.name}:${feed.length}`,
        name: e.name,
        domain: e.domain,
        items: d.items || [],
        sources: d.sources,
        error: d.error,
        status: e.status === 'error' ? 'error' : 'done',
      })
    }

    return { agentsByStep, stageStatus, doneCount, totalCount, feed }
  }, [events])

  // Elapsed-time ticker — concrete "the system is churning" proof during the
  // long decompose/synthesize phases where no agents are visibly moving.
  // Resets each run (when loading restarts) and freezes at the final time.
  const [elapsed, setElapsed] = useState(0)
  const startRef = useRef<number | null>(null)
  // Which agent's findings are expanded inline (click a finished pill to inspect).
  const [expanded, setExpanded] = useState<string | null>(null)
  // Which Live-Intelligence feed rows are expanded (collapsed by default; a Set
  // lets several rows stay open independently).
  const [feedOpen, setFeedOpen] = useState<Set<string>>(new Set())
  const toggleFeed = (key: string) =>
    setFeedOpen((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  useEffect(() => {
    if (!loading) {
      startRef.current = null
      return
    }
    if (startRef.current === null) startRef.current = Date.now()
    const tick = () => setElapsed(Math.round((Date.now() - (startRef.current as number)) / 1000))
    tick()
    const id = window.setInterval(tick, 1000)
    return () => window.clearInterval(id)
  }, [loading])

  // Coarse progress + ETA: decompose (indeterminate) → execute (by agents) → synthesize → done.
  const synthActive = stageStatus['synthesize'] === 'active'
  const pct = !loading
    ? 100
    : synthActive
      ? 94
      : totalCount > 0
        ? Math.min(90, 10 + (doneCount / totalCount) * 80)
        : 6
  const remainingSec =
    loading && pct > 12 && pct < 94 ? Math.max(0, Math.round((elapsed * (100 - pct)) / pct)) : null
  const etaLabel = !loading
    ? 'complete'
    : pct < 12
      ? 'estimating…'
      : synthActive
        ? 'finalizing…'
        : remainingSec != null
          ? `~${formatElapsed(remainingSec)} left`
          : ''

  // Latest streamed executive-summary text — fills the synthesize wait with the answer.
  let synthesisText = ''
  for (const e of events) if (e.type === 'synthesis') synthesisText = e.text

  if (events.length === 0 && loading) {
    return (
      <div className="bg-surface-container p-4 mb-6 border border-outline-variant/10">
        <div className="flex items-center gap-3">
          <span className="w-2 h-2 bg-accent animate-pulse-glow" />
          <span className="font-headline text-xs uppercase tracking-widest text-on-surface-variant">
            Initializing agent swarm
          </span>
          <span className="ml-auto font-mono text-[10px] text-outline tabular-nums">{formatElapsed(elapsed)}</span>
        </div>
      </div>
    )
  }

  if (events.length === 0) return null

  return (
    <div data-vn="swarm" className="bg-surface-container mb-6 border border-outline-variant/10">
      {/* Header: phase stepper + agent counter */}
      <div className="flex items-center justify-between gap-4 px-4 py-3 border-b border-outline-variant/10">
        <div className="flex items-center gap-2">
          <span className="font-headline text-xs font-bold uppercase tracking-widest text-on-surface">
            <span className="font-bold">Agent</span>{' '}
            <span className="font-normal">Swarm</span>
          </span>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            {STAGES.map((stage, i) => {
              const st = stageStatus[stage]
              // Glyph makes the single current step unmistakable: ✓ finished,
              // ● (pulsing) running now, ○ not started. Only the one active
              // stage pulses — finished stages read as done, not "still going".
              const glyph = st === 'done' ? '✓' : st === 'active' ? '●' : '○'
              return (
                <div key={stage} className="flex items-center gap-2">
                  <span className="flex items-center gap-1">
                    <span
                      className={`text-[10px] leading-none ${
                        st === 'done'
                          ? 'text-secondary'
                          : st === 'active'
                            ? 'text-accent-bright animate-pulse-glow'
                            : 'text-outline-variant'
                      }`}
                    >
                      {glyph}
                    </span>
                    <span
                      className={`font-mono text-[10px] uppercase tracking-wider ${
                        st === 'done'
                          ? 'text-on-surface-variant'
                          : st === 'active'
                            ? 'text-accent-bright font-bold animate-pulse-glow'
                            : 'text-outline'
                      }`}
                    >
                      {stage}
                    </span>
                  </span>
                  {i < STAGES.length - 1 && <span className="text-outline-variant text-[10px]">›</span>}
                </div>
              )
            })}
          </div>
          <span className="font-mono text-[10px] text-outline tabular-nums">
            {doneCount}/{totalCount} agents
          </span>
          <span className="flex items-center gap-1.5 font-mono text-[10px] tabular-nums text-on-surface-variant">
            {loading && <span className="w-1.5 h-1.5 bg-accent-bright animate-pulse-glow shrink-0" />}
            {formatElapsed(elapsed)}
          </span>
        </div>
      </div>

      {/* Progress bar + ETA */}
      <div className="px-4 py-2 border-b border-outline-variant/10 flex items-center gap-3">
        <div className="flex-1 h-1 bg-surface-container-high overflow-hidden">
          <div
            className={`h-full bg-accent-bright transition-all duration-500 ${pct < 12 ? 'animate-pulse-glow' : ''}`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="font-mono text-[9px] text-outline tabular-nums whitespace-nowrap">{etaLabel}</span>
      </div>

      {/* Plan = the swarm roster, grouped by step */}
      <div className="divide-y divide-outline-variant/10">
        {loading && agentsByStep.length === 0 && (
          <div className="px-4 py-3 flex items-center gap-2.5">
            <span className="w-1.5 h-1.5 bg-accent-bright animate-pulse-glow shrink-0" />
            <span className="text-[11px] text-on-surface-variant">
              Planning research strategy — decomposing the question into a multi-agent plan…
            </span>
          </div>
        )}
        {agentsByStep.map(({ step, description, agents }) => (
          <div key={step} className="px-4 py-3">
            <div className="flex items-baseline gap-2 mb-2.5">
              <span className="font-mono text-[10px] font-bold text-accent-bright tabular-nums">
                {String(step).padStart(2, '0')}
              </span>
              {description && (
                <span className="text-[11px] text-on-surface-variant leading-snug">{description}</span>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              {agents.map((a) => {
                const k = agentKey(a.step, a.name)
                const canExpand =
                  (a.status === 'done' || a.status === 'error') &&
                  !!(a.detail && (a.detail.items?.length || a.detail.error))
                return (
                  <AgentPill
                    key={k}
                    agent={a}
                    expanded={expanded === k}
                    onClick={canExpand ? () => setExpanded(expanded === k ? null : k) : undefined}
                  />
                )
              })}
            </div>
            {(() => {
              const a = agents.find((x) => agentKey(x.step, x.name) === expanded)
              if (!a || !a.detail) return null
              const meta = DOMAIN[a.domain] ?? DOMAIN.unknown
              return (
                <div
                  className="mt-2.5 ml-1 pl-3 border-l-2 animate-expand-down"
                  style={{ borderColor: hexA(a.status === 'error' ? 'rgba(255,255,255,0.4)' : meta.color, 0.5) }}
                >
                  <div className="font-mono text-[10px] text-on-surface mb-1">
                    {a.name}
                    {a.detail.confidence && (
                      <span className="text-outline"> · {a.detail.confidence} confidence</span>
                    )}
                  </div>
                  {a.detail.error ? (
                    <div className="text-[11px] text-outline italic">— {a.detail.error}</div>
                  ) : (
                    <ul className="space-y-0.5">
                      {a.detail.items.map((it, i) => (
                        <li key={i} className="text-[11px] text-on-surface-variant leading-snug flex gap-1.5">
                          <span className="text-outline shrink-0">›</span>
                          <span>{it}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {a.detail.sources && a.detail.sources.length > 0 && (
                    <div className="font-mono text-[9px] text-outline mt-1">
                      Sources: {a.detail.sources.join(' · ')}
                    </div>
                  )}
                </div>
              )
            })()}
          </div>
        ))}
      </div>

      {/* Live intelligence feed — each agent's findings as they land */}
      {feed.length > 0 && (
        <div className="border-t border-outline-variant/10 px-4 py-3">
          <div className="font-headline text-[10px] font-bold uppercase tracking-widest text-on-surface-variant mb-2.5">
            Live Intelligence <span className="text-outline">· {feed.length}</span>
          </div>
          <div className="space-y-2.5">
            {feed.map((f) => {
              const meta = DOMAIN[f.domain] ?? DOMAIN.unknown
              // Failed sources render muted (calm "no data"), not alarming red.
              const tone = f.status === 'error' ? 'rgba(255,255,255,0.4)' : meta.color
              const open = feedOpen.has(f.key)
              // Compact summary so a collapsed row is still informative.
              const summary = f.error ? f.error : `${f.items.length} finding${f.items.length === 1 ? '' : 's'}`
              return (
                <div key={f.key} className="animate-slide-in-right">
                  <div
                    onClick={() => toggleFeed(f.key)}
                    className="flex items-center gap-2 cursor-pointer hover:brightness-125 transition-all select-none"
                  >
                    <span
                      className="material-symbols-outlined text-sm shrink-0 text-outline transition-transform"
                      style={{ transform: open ? 'rotate(90deg)' : 'none' }}
                    >
                      chevron_right
                    </span>
                    <span className="w-1.5 h-1.5 shrink-0" style={{ backgroundColor: tone }} />
                    <span className="font-mono text-[10px] text-on-surface whitespace-nowrap">{f.name}</span>
                    <span
                      className="font-mono text-[9px] font-bold uppercase tracking-wider whitespace-nowrap"
                      style={{ color: tone }}
                    >
                      {meta.label}
                    </span>
                    {!open && (
                      <span className="font-mono text-[9px] text-outline whitespace-nowrap">· {summary}</span>
                    )}
                    {f.sources && f.sources.length > 0 && (
                      <span className="font-mono text-[9px] text-outline ml-auto whitespace-nowrap truncate">
                        {f.sources.join(' · ')}
                      </span>
                    )}
                  </div>
                  {open &&
                    (f.error ? (
                      <div className="text-[11px] text-outline italic pl-7 leading-snug animate-expand-down">
                        — {f.error}
                      </div>
                    ) : (
                      <ul className="pl-7 space-y-0.5 mt-0.5 animate-expand-down">
                        {f.items.map((it, i) => (
                          <li key={i} className="text-[11px] text-on-surface-variant leading-snug flex gap-1.5">
                            <span className="text-outline shrink-0">›</span>
                            <span>{it}</span>
                          </li>
                        ))}
                      </ul>
                    ))}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {loading && stageStatus['synthesize'] === 'active' && (
        <div className="border-t border-outline-variant/10 px-4 py-3">
          <div className="flex items-center gap-2.5 mb-1.5">
            <span className="w-1.5 h-1.5 bg-accent-bright animate-pulse-glow shrink-0" />
            <span className="font-headline text-[10px] uppercase tracking-widest text-on-surface-variant">
              {synthesisText ? 'Executive assessment — drafting' : 'Synthesizing findings into the final assessment…'}
            </span>
          </div>
          {synthesisText && (
            <p className="text-[12px] text-on-surface leading-relaxed">
              {synthesisText}
              <span className="animate-pulse-glow">▍</span>
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function AgentPill({
  agent,
  onClick,
  expanded,
}: {
  agent: AgentState
  onClick?: () => void
  expanded?: boolean
}) {
  const meta = DOMAIN[agent.domain] ?? DOMAIN.unknown
  const isRunning = agent.status === 'running'
  const isError = agent.status === 'error'
  const isPending = agent.status === 'pending'
  // Failed sources render muted grey ("no data"), not alarming red.
  const tone = isError ? 'rgba(255,255,255,0.4)' : meta.color

  return (
    <div
      onClick={onClick}
      className={`inline-flex items-center gap-2 border px-2.5 py-1.5 transition-opacity ${
        isPending ? 'opacity-45' : 'opacity-100'
      } ${isRunning ? 'animate-agent-churn' : ''} ${onClick ? 'cursor-pointer hover:brightness-125' : ''}`}
      style={
        {
          borderColor: expanded
            ? hexA(tone, 0.9)
            : isPending
              ? 'rgba(255,255,255,0.1)'
              : hexA(tone, isRunning ? 0.7 : 0.35),
          backgroundColor: isPending ? 'transparent' : hexA(tone, expanded ? 0.18 : 0.1),
          ...(isRunning ? { '--churn': hexA(tone, 0.55) } : {}),
        } as CSSProperties
      }
      title={onClick ? 'Click to inspect findings' : (agent.task ?? agent.name)}
    >
      {/* Status dot */}
      <span
        className={`w-1.5 h-1.5 shrink-0 ${isRunning ? 'animate-pulse-glow' : ''}`}
        style={{
          backgroundColor: isPending ? 'rgba(255,255,255,0.3)' : tone,
        }}
      />
      <span className="font-mono text-[10px] text-on-surface whitespace-nowrap">{agent.name}</span>
      {!isPending && (
        <span
          className="font-mono text-[9px] font-bold uppercase tracking-wider whitespace-nowrap"
          style={{ color: tone }}
        >
          {meta.label}
        </span>
      )}
      {/* Result chip */}
      {agent.status === 'done' && agent.summary && (
        <span className="font-mono text-[9px] text-outline whitespace-nowrap">
          {agent.summary}
          {agent.ms != null && ` · ${formatMs(agent.ms)}`}
        </span>
      )}
      {isError && (
        <span className="font-mono text-[9px] uppercase tracking-wider text-outline whitespace-nowrap">no data</span>
      )}
      {isRunning && (
        <span className="font-mono text-[9px] uppercase tracking-wider text-outline whitespace-nowrap">
          running
        </span>
      )}
    </div>
  )
}

/** Apply an alpha to a hex or rgba() colour string for the pill tints. */
function hexA(color: string, alpha: number): string {
  if (color.startsWith('#') && color.length === 7) {
    const a = Math.round(alpha * 255)
      .toString(16)
      .padStart(2, '0')
    return `${color}${a}`
  }
  // already rgba()/other — fall back to a neutral tint
  return `rgba(255,255,255,${alpha * 0.6})`
}

function formatMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

/** Elapsed run time as m:ss (e.g. 92 → "1:32"). */
function formatElapsed(s: number): string {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
