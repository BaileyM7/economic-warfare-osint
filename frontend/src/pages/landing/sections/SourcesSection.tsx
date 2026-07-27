import { StatCard } from '../../../brand';
import Reveal from '../components/Reveal';

const SOURCES = [
  'OpenSanctions',
  'OFAC SDN',
  'OpenCorporates',
  'GLEIF',
  'ICIJ Offshore Leaks',
  'SEC EDGAR',
  'yfinance',
  'FRED',
  'UN Comtrade',
  'UNCTADstat',
  'GDELT',
  'ACLED',
  'IMF',
  'World Bank',
  'Sayari',
];

export default function SourcesSection() {
  return (
    <section className="border-t border-outline-variant bg-surface-container-low">
      <div className="mx-auto max-w-7xl px-6 py-20 lg:px-10 lg:py-28">
        <Reveal>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <StatCard
              value="41"
              label="Registered tools"
              color="blue"
              className="border border-outline-variant"
            />
            <StatCard
              value="9"
              label="Intelligence domains"
              color="light"
              className="border border-outline-variant"
            />
            <StatCard
              value="15"
              label="Open data sources"
              color="orange"
              className="border border-outline-variant"
            />
          </div>
        </Reveal>
        <Reveal delay={150}>
          <div className="mt-12 border-t border-outline-variant pt-8">
            <p className="mb-5 font-mono text-[11px] uppercase tracking-[0.25em] text-outline">
              Fused from open sources
            </p>
            <ul className="flex flex-wrap gap-x-8 gap-y-3">
              {SOURCES.map((s) => (
                <li key={s} className="font-mono text-[13px] text-on-surface-variant">
                  {s}
                </li>
              ))}
            </ul>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
