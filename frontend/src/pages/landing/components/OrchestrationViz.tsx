import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useInView, usePrefersReducedMotion } from '../hooks';

/**
 * Animated mirror of the real Ask Anything pipeline
 * (src/orchestrator/main.py): recall -> decompose -> parallel tool fan-out
 * across the 9 tool domains -> synthesize. Pure SVG + CSS motion-path pulses;
 * a timeout-driven loop replays the lifecycle (~13s) while on screen.
 * Reduced motion renders the completed frame, no animation.
 */

// Keep in sync with the DOMAIN map in frontend/src/components/SwarmPanel.tsx
// (colors) and src/orchestrator/tool_registry.py (domains/sources).
const DOMAINS = [
  { key: 'sanctions', label: 'Sanctions', color: '#ff5a58', sources: 'OFAC · OpenSanctions' },
  {
    key: 'corporate',
    label: 'Corporate',
    color: '#5b9bd4',
    sources: 'OpenCorporates · GLEIF · ICIJ',
  },
  { key: 'market', label: 'Market', color: '#efb16a', sources: 'SEC EDGAR · yfinance' },
  { key: 'trade', label: 'Trade', color: '#a9d8fb', sources: 'UN Comtrade · UNCTAD' },
  { key: 'geopolitical', label: 'Geopolitical', color: '#d23c3a', sources: 'GDELT · ACLED' },
  { key: 'economic', label: 'Economic', color: '#0092ff', sources: 'FRED · IMF · World Bank' },
  { key: 'sayari', label: 'Sayari', color: '#efb16a', sources: 'Sayari Graph' },
  { key: 'news', label: 'News', color: 'rgba(255,255,255,0.6)', sources: 'Global press' },
  {
    key: 'graph',
    label: 'Graph',
    color: 'rgba(255,255,255,0.7)',
    sources: 'Emissary knowledge graph',
  },
] as const;

type DomainKey = (typeof DOMAINS)[number]['key'];
type DomainStatus = 'idle' | 'pending' | 'running' | 'done';
type Stage = 'typing' | 'recall' | 'decompose' | 'fanout' | 'synthesize' | 'answer';

// The four canonical pre-warmed demo queries (src/routers/orchestrator.py).
const QUERIES = [
  'What happens to global semiconductor supply if we sanction Fujian Jinhua?',
  'Who ultimately owns Nuctech, and what are its sanctions exposures?',
  'How exposed is the drone supply chain to a DJI export ban?',
  'Map Rosatom’s subsidiaries and their Western trade links.',
];

// Layout (viewBox coordinates).
const VB = { w: 1200, h: 560 };
const X = { query: 80, recall: 260, decompose: 440, domain: 740, synth: 960, answer: 1120 };
const MID_Y = VB.h / 2;
const domainY = (i: number) => 62 + (i * (VB.h - 124)) / (DOMAINS.length - 1);

function edgePath(x1: number, y1: number, x2: number, y2: number): string {
  const mx = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
}

interface LoopState {
  stage: Stage;
  typed: string;
  domains: Record<DomainKey, DomainStatus>;
}

const ALL_IDLE = Object.fromEntries(DOMAINS.map((d) => [d.key, 'idle'])) as Record<
  DomainKey,
  DomainStatus
>;
const ALL_DONE = Object.fromEntries(DOMAINS.map((d) => [d.key, 'done'])) as Record<
  DomainKey,
  DomainStatus
>;

function useOrchestrationLoop(active: boolean): LoopState {
  const [state, setState] = useState<LoopState>({ stage: 'typing', typed: '', domains: ALL_IDLE });
  const queryIndex = useRef(0);

  useEffect(() => {
    if (!active) return;
    const timers = new Set<ReturnType<typeof setTimeout>>();
    let cancelled = false;
    const after = (ms: number, fn: () => void) => {
      const t = setTimeout(() => {
        timers.delete(t);
        if (!cancelled) fn();
      }, ms);
      timers.add(t);
    };

    const runLoop = () => {
      const query = QUERIES[queryIndex.current % QUERIES.length];
      queryIndex.current += 1;
      setState({ stage: 'typing', typed: '', domains: ALL_IDLE });

      // Typewriter.
      const charMs = 28;
      for (let i = 1; i <= query.length; i++) {
        after(i * charMs, () => setState((s) => ({ ...s, typed: query.slice(0, i) })));
      }
      let t = query.length * charMs + 500;

      after(t, () => setState((s) => ({ ...s, stage: 'recall' })));
      t += 900;
      after(t, () => setState((s) => ({ ...s, stage: 'decompose' })));
      t += 1100;

      // Fan-out: domains appear pending, then start staggered and finish on
      // randomized durations — mirrors the two-level parallel execute phase.
      const order = [...DOMAINS.map((d) => d.key)].sort(() => Math.random() - 0.5);
      after(t, () =>
        setState((s) => ({
          ...s,
          stage: 'fanout',
          domains: Object.fromEntries(order.map((k) => [k, 'pending'])) as Record<
            DomainKey,
            DomainStatus
          >,
        })),
      );
      let lastFinish = 0;
      order.forEach((key, i) => {
        const start = 150 + i * 130;
        const runFor = 1600 + Math.random() * 2400;
        lastFinish = Math.max(lastFinish, start + runFor);
        after(t + start, () =>
          setState((s) => ({ ...s, domains: { ...s.domains, [key]: 'running' } })),
        );
        after(t + start + runFor, () =>
          setState((s) => ({ ...s, domains: { ...s.domains, [key]: 'done' } })),
        );
      });
      t += lastFinish + 300;

      after(t, () => setState((s) => ({ ...s, stage: 'synthesize' })));
      t += 2000;
      after(t, () => setState((s) => ({ ...s, stage: 'answer' })));
      t += 3000;
      after(t, runLoop);
    };

    runLoop();
    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
  }, [active]);

  return state;
}

/** Repeating pulse traveling along an SVG path via CSS motion path. */
function Pulse({
  d,
  color,
  dur,
  delay = 0,
}: {
  d: string;
  color: string;
  dur: number;
  delay?: number;
}) {
  return (
    <circle
      r={3}
      fill={color}
      style={{
        offsetPath: `path("${d}")`,
        offsetRotate: '0deg',
        animation: `landing-pulse-travel ${dur}s linear ${delay}s infinite`,
        opacity: 0,
      }}
    />
  );
}

function StageBox({
  x,
  label,
  active,
  done,
}: {
  x: number;
  label: string;
  active: boolean;
  done: boolean;
}) {
  return (
    <g>
      <rect
        x={x - 52}
        y={MID_Y - 20}
        width={104}
        height={40}
        fill={active ? '#1f3864' : '#0b2451'}
        stroke={active ? '#5b9bd4' : done ? 'rgba(255,255,255,0.35)' : 'rgba(255,255,255,0.12)'}
        strokeWidth={1}
      />
      <text
        x={x}
        y={MID_Y + 4}
        textAnchor="middle"
        fill={active || done ? '#ffffff' : 'rgba(255,255,255,0.55)'}
        style={{ font: '600 11px "Fira Code", monospace', letterSpacing: '0.14em' }}
      >
        {label}
      </text>
    </g>
  );
}

export default function OrchestrationViz({ className = '' }: { className?: string }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const reducedMotion = usePrefersReducedMotion();
  const inView = useInView(wrapRef, { threshold: 0.35, once: false });
  const live = useOrchestrationLoop(inView && !reducedMotion);

  const state: LoopState = reducedMotion
    ? { stage: 'answer', typed: QUERIES[0], domains: ALL_DONE }
    : live;
  const { stage, typed, domains } = state;

  const stageReached = (s: Stage) => {
    const order: Stage[] = ['typing', 'recall', 'decompose', 'fanout', 'synthesize', 'answer'];
    return order.indexOf(stage) >= order.indexOf(s);
  };

  return (
    <div ref={wrapRef} className={className}>
      {/* Typewriter query line */}
      <div className="mb-5 border border-outline-variant bg-surface-container-low px-4 py-3">
        <span className="font-mono text-[13px] text-on-surface-variant">
          <span className="text-primary">analyst@emissary</span>
          <span className="text-outline"> $ </span>
          {typed}
          {stage === 'typing' && !reducedMotion && (
            <span className="landing-caret text-on-surface">▍</span>
          )}
        </span>
      </div>

      <svg
        viewBox={`0 0 ${VB.w} ${VB.h}`}
        className="w-full"
        role="img"
        aria-label="Diagram of the Emissary orchestrator: a query is decomposed and fanned out in parallel across nine intelligence domains, then synthesized into an assessment"
      >
        {/* ---- Edges ---- */}
        {(() => {
          const edges: ReactNode[] = [];
          const base = 'rgba(255,255,255,0.10)';

          const trunk: Array<{ d: string; on: boolean; color: string }> = [
            {
              d: edgePath(X.query + 52, MID_Y, X.recall - 52, MID_Y),
              on: stage === 'recall',
              color: '#5b9bd4',
            },
            {
              d: edgePath(X.recall + 52, MID_Y, X.decompose - 52, MID_Y),
              on: stage === 'decompose',
              color: '#5b9bd4',
            },
            {
              d: edgePath(X.synth + 52, MID_Y, X.answer - 34, MID_Y),
              on: stage === 'answer',
              color: '#5b9bd4',
            },
          ];
          trunk.forEach((e, i) => {
            edges.push(
              <path
                key={`trunk-${i}`}
                d={e.d}
                fill="none"
                stroke={e.on ? e.color : base}
                strokeWidth={1}
                strokeDasharray="2 6"
                className={e.on ? 'landing-edge-active' : undefined}
              />,
            );
            if (e.on && !reducedMotion) {
              edges.push(<Pulse key={`trunk-pulse-${i}`} d={e.d} color={e.color} dur={0.8} />);
            }
          });

          DOMAINS.forEach((dom, i) => {
            const y = domainY(i);
            const status = domains[dom.key];
            const inPath = edgePath(X.decompose + 52, MID_Y, X.domain - 14, y);
            const outPath = edgePath(X.domain + 14, y, X.synth - 52, MID_Y);
            const inActive = status === 'running';
            const outActive = status === 'done' && (stage === 'fanout' || stage === 'synthesize');
            edges.push(
              <path
                key={`in-${dom.key}`}
                d={inPath}
                fill="none"
                stroke={inActive ? dom.color : base}
                strokeOpacity={inActive ? 0.8 : 1}
                strokeWidth={1}
                strokeDasharray="2 6"
                className={inActive ? 'landing-edge-active' : undefined}
              />,
              <path
                key={`out-${dom.key}`}
                d={outPath}
                fill="none"
                stroke={outActive ? dom.color : base}
                strokeOpacity={outActive ? 0.8 : 1}
                strokeWidth={1}
                strokeDasharray="2 6"
                className={outActive ? 'landing-edge-active' : undefined}
              />,
            );
            if (!reducedMotion && inActive) {
              edges.push(
                <Pulse
                  key={`in-pulse-${dom.key}`}
                  d={inPath}
                  color={dom.color}
                  dur={1.1}
                  delay={-(i * 0.23)}
                />,
              );
            }
            if (!reducedMotion && outActive) {
              edges.push(
                <Pulse
                  key={`out-pulse-${dom.key}`}
                  d={outPath}
                  color={dom.color}
                  dur={1.1}
                  delay={-(i * 0.31)}
                />,
              );
            }
          });
          return edges;
        })()}

        {/* ---- Stage nodes ---- */}
        <g>
          <rect
            x={X.query - 52}
            y={MID_Y - 20}
            width={104}
            height={40}
            fill="#0b2451"
            stroke={stage === 'typing' ? '#5b9bd4' : 'rgba(255,255,255,0.35)'}
          />
          <text
            x={X.query}
            y={MID_Y + 4}
            textAnchor="middle"
            fill="#ffffff"
            style={{ font: '600 11px "Fira Code", monospace', letterSpacing: '0.14em' }}
          >
            QUERY
          </text>
        </g>
        <StageBox
          x={X.recall}
          label="RECALL"
          active={stage === 'recall'}
          done={stageReached('decompose')}
        />
        <StageBox
          x={X.decompose}
          label="DECOMPOSE"
          active={stage === 'decompose'}
          done={stageReached('fanout')}
        />
        <StageBox
          x={X.synth}
          label="SYNTHESIZE"
          active={stage === 'synthesize'}
          done={stage === 'answer'}
        />

        {/* Synthesize streaming bar */}
        {stage === 'synthesize' && !reducedMotion && (
          <rect
            x={X.synth - 52}
            y={MID_Y + 24}
            width={104}
            height={3}
            fill="#5b9bd4"
            style={{
              transformOrigin: `${X.synth - 52}px ${MID_Y + 24}px`,
              animation: 'landing-stream 2s cubic-bezier(0.22, 1, 0.36, 1) forwards',
            }}
          />
        )}

        {/* ---- Domain nodes ---- */}
        {DOMAINS.map((dom, i) => {
          const y = domainY(i);
          const status = domains[dom.key];
          const dim = status === 'idle' || status === 'pending';
          return (
            <g key={dom.key} opacity={status === 'idle' ? 0.35 : 1}>
              {status === 'running' && (
                <circle
                  cx={X.domain}
                  cy={y}
                  r={7}
                  fill="none"
                  stroke={dom.color}
                  className="landing-node-running"
                />
              )}
              <rect
                x={X.domain - 5}
                y={y - 5}
                width={10}
                height={10}
                fill={dim ? '#0b2451' : dom.color}
                stroke={dim ? 'rgba(255,255,255,0.3)' : dom.color}
              />
              <text
                x={X.domain + 18}
                y={y - 1}
                fill={dim ? 'rgba(255,255,255,0.55)' : '#ffffff'}
                style={{ font: '600 11px "Fira Code", monospace', letterSpacing: '0.1em' }}
              >
                {dom.label.toUpperCase()}
              </text>
              <text
                x={X.domain + 18}
                y={y + 12}
                fill="rgba(255,255,255,0.4)"
                className="landing-viz-sources"
                style={{ font: '400 9px "Fira Code", monospace' }}
              >
                {dom.sources}
              </text>
            </g>
          );
        })}

        {/* ---- Answer card ---- */}
        <g opacity={stage === 'answer' ? 1 : 0.25} style={{ transition: 'opacity 0.6s ease' }}>
          <rect
            x={X.answer - 34}
            y={MID_Y - 44}
            width={100}
            height={88}
            fill="#0b2451"
            stroke="rgba(255,255,255,0.35)"
          />
          <rect x={X.answer - 22} y={MID_Y - 30} width={64} height={5} fill="#d23c3a" />
          <rect
            x={X.answer - 22}
            y={MID_Y - 16}
            width={76}
            height={4}
            fill="rgba(255,255,255,0.45)"
          />
          <rect
            x={X.answer - 22}
            y={MID_Y - 6}
            width={70}
            height={4}
            fill="rgba(255,255,255,0.3)"
          />
          <rect
            x={X.answer - 22}
            y={MID_Y + 4}
            width={74}
            height={4}
            fill="rgba(255,255,255,0.3)"
          />
          <rect
            x={X.answer - 22}
            y={MID_Y + 14}
            width={58}
            height={4}
            fill="rgba(255,255,255,0.3)"
          />
          <text
            x={X.answer + 16}
            y={MID_Y + 36}
            textAnchor="middle"
            fill="rgba(255,255,255,0.55)"
            style={{ font: '600 9px "Fira Code", monospace', letterSpacing: '0.14em' }}
          >
            ASSESSMENT
          </text>
        </g>
      </svg>
    </div>
  );
}
