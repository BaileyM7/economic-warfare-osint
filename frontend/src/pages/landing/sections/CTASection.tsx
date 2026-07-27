import { useNavigate } from 'react-router-dom';
import { Button, MotifHeading } from '../../../brand';
import Reveal from '../components/Reveal';
import { launchTarget } from '../hooks';

const ArrowGlyph = () => (
  <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
    <path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" fill="none" strokeWidth="1.5" />
  </svg>
);

export default function CTASection() {
  const navigate = useNavigate();

  return (
    <section className="tactical-grid border-t border-outline-variant bg-background">
      <div className="mx-auto max-w-7xl px-6 py-28 lg:px-10 lg:py-40">
        <Reveal>
          <h2 className="landing-h1 max-w-3xl text-on-surface">
            <MotifHeading bold="Decision-grade intelligence," light="on demand" />
          </h2>
          <p className="landing-lead mt-6 max-w-xl font-body text-on-surface-variant">
            Sign in and put the swarm to work on your next question.
          </p>
          <div className="mt-10">
            <Button variant="primary" icon={<ArrowGlyph />} onClick={() => navigate(launchTarget())}>
              Launch app
            </Button>
          </div>
        </Reveal>
      </div>
      <footer className="border-t border-outline-variant">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-6 py-6 lg:px-10">
          <span className="font-headline text-[11px] uppercase tracking-[0.2em] text-outline">
            <span className="font-bold">Agile</span> <span className="font-normal">Defense</span> ·
            Emissary
          </span>
          <span className="font-mono text-[11px] text-outline">
            Open-source intelligence, fused for economic-warfare analysis
          </span>
        </div>
      </footer>
    </section>
  );
}
