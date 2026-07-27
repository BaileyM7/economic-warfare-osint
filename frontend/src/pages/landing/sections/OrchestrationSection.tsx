import { MotifHeading } from '../../../brand';
import OrchestrationViz from '../components/OrchestrationViz';
import Reveal from '../components/Reveal';

export default function OrchestrationSection() {
  return (
    <section
      id="orchestration"
      className="border-t border-outline-variant bg-surface-container-low"
    >
      <div className="mx-auto max-w-7xl px-6 py-24 lg:px-10 lg:py-36">
        <Reveal>
          <p className="mb-4 font-mono text-[12px] uppercase tracking-[0.3em] text-outline">
            The orchestrator
          </p>
          <h2 className="landing-h1 text-on-surface">
            <MotifHeading bold="One question," light="every source" />
          </h2>
          <p className="landing-lead mt-6 max-w-2xl font-body text-on-surface-variant">
            An analyst asks in plain language. The orchestrator recalls prior findings, decomposes
            the question into a plan, and dispatches dozens of agents across nine intelligence
            domains at once. Their results converge into a single synthesized assessment.
          </p>
        </Reveal>
        <Reveal delay={150} className="mt-14 hidden md:block">
          <OrchestrationViz />
        </Reveal>
        {/* Compact stage list where the wide diagram cannot fit. */}
        <Reveal delay={150} className="mt-12 md:hidden">
          <ol className="space-y-3 border-l border-outline-variant pl-5 font-mono text-[13px] text-on-surface-variant">
            <li>RECALL — prior findings from analyst memory</li>
            <li>DECOMPOSE — the question becomes a plan of steps</li>
            <li>EXECUTE — 41 tools fan out across 9 domains in parallel</li>
            <li>SYNTHESIZE — findings fuse into one assessment</li>
          </ol>
        </Reveal>
      </div>
    </section>
  );
}
