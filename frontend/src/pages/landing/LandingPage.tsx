import './landing.css';
import LandingNav from './LandingNav';
import HeroSection from './sections/HeroSection';
import OrchestrationSection from './sections/OrchestrationSection';
import FeaturesSection from './sections/FeaturesSection';
import SourcesSection from './sections/SourcesSection';
import CTASection from './sections/CTASection';

/**
 * Public marketing page served at "/" for unauthenticated visitors (see the
 * LandingGate in App.tsx). Self-contained: everything under pages/landing/
 * ships in this lazy chunk and nothing here requires auth or the AppShell.
 */
export default function LandingPage() {
  return (
    <div className="min-h-screen bg-background font-body text-on-surface">
      <LandingNav />
      <main>
        <HeroSection />
        <OrchestrationSection />
        <FeaturesSection />
        <SourcesSection />
        <CTASection />
      </main>
    </div>
  );
}
