import { useNavigate } from 'react-router-dom';
import { Button } from '../../../brand';
import TessellationCanvas from '../components/TessellationCanvas';
import Reveal from '../components/Reveal';

const ArrowGlyph = () => (
  <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
    <path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" fill="none" strokeWidth="1.5" />
  </svg>
);

export default function HeroSection() {
  const navigate = useNavigate();

  return (
    <section className="relative flex min-h-screen flex-col justify-end overflow-hidden bg-background">
      <TessellationCanvas className="absolute inset-0 h-full w-full" />
      {/* Bottom fade so hero text sits on solid navy. */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            'linear-gradient(180deg, rgba(6,16,51,0.3) 0%, rgba(6,16,51,0) 40%, rgba(6,16,51,0.88) 100%)',
        }}
      />
      <div className="relative mx-auto w-full max-w-7xl px-6 pb-24 pt-40 lg:px-10 lg:pb-32">
        <Reveal>
          <p className="mb-6 font-mono text-[12px] uppercase tracking-[0.3em] text-outline">
            Economic-warfare intelligence
          </p>
          <h1 className="font-headline text-on-surface">
            <span className="landing-hero-line block font-medium text-accent-hover">Ask</span>
            <span className="landing-hero-line block font-normal">Anything</span>
          </h1>
        </Reveal>
        <Reveal delay={150}>
          <p className="landing-lead mt-7 max-w-2xl font-body text-on-surface-variant">
            Emissary turns a single question into a decision-grade impact assessment. A swarm of AI
            agents queries fifteen open sources in parallel — sanctions, corporate registries, trade
            flows, markets, conflict data — and fuses the findings into one structured answer,
            complete with an entity graph.
          </p>
        </Reveal>
        <Reveal delay={300}>
          <div className="mt-10 flex flex-wrap items-center gap-5">
            <Button variant="primary" icon={<ArrowGlyph />} onClick={() => navigate('/login')}>
              Launch app
            </Button>
            <Button
              variant="ghost"
              onClick={() =>
                document.getElementById('orchestration')?.scrollIntoView({ behavior: 'smooth' })
              }
            >
              See how it works
            </Button>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
