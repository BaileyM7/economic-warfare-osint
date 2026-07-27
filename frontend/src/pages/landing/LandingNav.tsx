import { Link, useNavigate } from 'react-router-dom';
import { Button } from '../../brand';
import { useScrolledPast } from './hooks';

export default function LandingNav() {
  const navigate = useNavigate();
  const scrolled = useScrolledPast(24);

  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 transition-colors duration-300 ${
        scrolled ? 'bg-background/95 border-b border-outline-variant' : 'bg-transparent'
      }`}
    >
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6 lg:px-10">
        <Link to="/" className="flex items-baseline gap-3">
          <span className="hidden font-headline text-[11px] uppercase tracking-[0.2em] text-on-surface-variant sm:inline">
            <span className="font-bold">Agile</span> <span className="font-normal">Defense</span>
          </span>
          <span className="font-headline text-lg font-bold uppercase tracking-[0.3em] text-on-surface">
            Emissary
          </span>
        </Link>
        <div className="flex items-center gap-6">
          <a
            href="#capabilities"
            className="hidden font-label text-sm uppercase tracking-wide text-on-surface-variant transition-colors hover:text-on-surface sm:block"
          >
            Capabilities
          </a>
          <Button
            variant="primary"
            className="h-9 whitespace-nowrap px-4 text-xs"
            onClick={() => navigate('/login')}
          >
            Launch app
          </Button>
        </div>
      </div>
    </header>
  );
}
