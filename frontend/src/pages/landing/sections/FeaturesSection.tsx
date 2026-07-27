import { MotifHeading } from '../../../brand';
import FeatureVideo from '../components/FeatureVideo';
import Reveal from '../components/Reveal';

interface Feature {
  slug: string;
  eyebrow: string;
  bold: string;
  light: string;
  body: string;
  video?: string;
  poster?: string;
}

// Demo clips are recorded via demo-videos/ (auto-loom pipeline); until a
// clip exists the poster renders alone. Posters live in public/videos/.
const FEATURES: Feature[] = [
  {
    slug: 'ask-anything',
    eyebrow: 'Deep analysis',
    bold: 'Ask',
    light: 'Anything',
    body: 'Free-form questions answered by the live agent swarm. Watch the plan light up as sanctions, corporate, trade, and market agents report in — then explore the fused entity graph behind the answer.',
    video: '/videos/ask-anything.mp4',
    poster: '/videos/ask-anything.jpg',
  },
  {
    slug: 'risk-feed',
    eyebrow: 'Proactive surfacing',
    bold: 'Risk',
    light: 'Feed',
    body: 'Risk shifts surface themselves. Cards rank by priority, and any card translates into a Course of Action with one click.',
    video: '/videos/risk-feed.mp4',
    poster: '/videos/risk-feed.jpg',
  },
  {
    slug: 'knowledge-graph',
    eyebrow: 'Shared memory',
    bold: 'Knowledge',
    light: 'Graph',
    body: 'Every analysis accumulates into the team’s persistent entity graph. Cluster by community, focus on any entity’s ego network, expand through Sayari, or pivot to map and matrix views.',
    video: '/videos/knowledge-graph.mp4',
    poster: '/videos/knowledge-graph.jpg',
  },
  {
    slug: 'monitoring',
    eyebrow: 'Live posture',
    bold: 'Continuous',
    light: 'Monitoring',
    body: 'Live KPIs stream over WebSocket onto a global map. Watchlists keep priority entities under standing observation.',
    video: '/videos/monitoring.mp4',
    poster: '/videos/monitoring.jpg',
  },
  {
    slug: 'coa',
    eyebrow: 'From insight to action',
    bold: 'Course of',
    light: 'Action',
    body: 'Model economic and tactical escalation paths in a drag-and-drop workspace, then export mission briefings with inline citations.',
    video: '/videos/coa.mp4',
    poster: '/videos/coa.jpg',
  },
  {
    slug: 'wargame',
    eyebrow: 'Simulation',
    bold: 'Wargame',
    light: 'the outcome',
    body: 'Compose a scenario and let agent factions play it out on a live WebGL globe — cyber, kinetic, economic, and information moves arcing between capitals in real time.',
    video: '/videos/wargame.mp4',
    poster: '/videos/wargame.jpg',
  },
];

export default function FeaturesSection() {
  return (
    <section id="capabilities" className="border-t border-outline-variant bg-background">
      <div className="mx-auto max-w-7xl px-6 py-24 lg:px-10 lg:py-36">
        <Reveal>
          <p className="mb-4 font-mono text-[12px] uppercase tracking-[0.3em] text-outline">
            Capabilities
          </p>
          <h2 className="landing-h1 text-on-surface">
            <MotifHeading bold="Built for" light="the mission" />
          </h2>
        </Reveal>
        <div className="mt-16 space-y-24 lg:mt-24 lg:space-y-32">
          {FEATURES.map((f, i) => (
            <Reveal key={f.slug}>
              <div
                className={`flex flex-col gap-8 lg:items-center lg:gap-16 ${
                  i % 2 === 1 ? 'lg:flex-row-reverse' : 'lg:flex-row'
                }`}
              >
                <div className="lg:w-5/12">
                  <p className="mb-3 font-mono text-[11px] uppercase tracking-[0.25em] text-outline">
                    {f.eyebrow}
                  </p>
                  <h3 className="font-headline text-3xl text-on-surface lg:text-4xl">
                    <MotifHeading bold={f.bold} light={f.light} />
                  </h3>
                  <p className="mt-5 font-body text-base leading-relaxed text-on-surface-variant">
                    {f.body}
                  </p>
                </div>
                <div className="lg:w-7/12">
                  <FeatureVideo src={f.video} poster={f.poster} label={`${f.bold} ${f.light}`} />
                </div>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
