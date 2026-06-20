import type { ReactNode } from 'react';
import type { TargetInfo } from '../types';

interface Props {
  target: TargetInfo;
  /** Optional content rendered below the Sanctions Status card in the right column. */
  extra?: ReactNode;
}

export default function ImpactInfoCards({ target, extra }: Props) {
  const sanctions = target.sanctions_status;
  const isSanctioned = sanctions.is_sanctioned;
  const priceUnavailable = target.price_unavailable || target.current_price == null;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6 items-stretch">
      {/* Target Company — full height of the row */}
      <div className="bg-surface-container rounded-xl p-6 relative overflow-hidden">
        <div className="absolute top-0 left-0 w-1 h-full bg-primary" />
        <h3 className="text-[10px] font-label uppercase tracking-widest text-outline mb-3">
          Target Company
        </h3>
        <div className="text-xl font-headline font-bold text-on-surface mb-1">
          {target.name || target.ticker}
        </div>
        <div className="text-xs text-outline">
          {target.ticker} &mdash; {target.sector || 'N/A'} &mdash; {target.country || 'N/A'}
        </div>
        <div className="mt-3 text-sm text-on-surface">
          Current Price:{' '}
          {priceUnavailable ? (
            <span className="text-outline italic">Live data temporarily unavailable</span>
          ) : (
            <strong className="font-mono">${(target.current_price as number).toFixed(2)}</strong>
          )}
        </div>
        {target.market_cap && (
          <div className="text-xs text-outline mt-1">
            Market Cap: ${(target.market_cap / 1e9).toFixed(1)}B
          </div>
        )}
      </div>

      {/* Right column: compact Sanctions Status + extra slot */}
      <div className="flex flex-col gap-4">
        <div className="bg-surface-container rounded-xl p-4 relative overflow-hidden">
          <div
            className={`absolute top-0 left-0 w-1 h-full ${isSanctioned ? 'bg-error' : 'bg-secondary'}`}
          />
          <h3 className="text-[10px] font-label uppercase tracking-widest text-outline mb-2">
            Sanctions Status
          </h3>
          <div className="mb-2">
            <span
              className={`px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest border ${
                isSanctioned
                  ? 'bg-error-container text-on-error-container border-error/30'
                  : 'bg-secondary-container/20 text-secondary border-secondary/30'
              }`}
            >
              {isSanctioned ? 'Sanctioned' : 'Not Currently Sanctioned'}
            </span>
          </div>
          {sanctions.lists.length > 0 && (
            <div className="text-xs text-on-surface">Lists: {sanctions.lists.join(', ')}</div>
          )}
          {sanctions.programs.length > 0 && (
            <div className="text-xs text-outline mt-0.5">
              Programs: {sanctions.programs.slice(0, 3).join(', ')}
            </div>
          )}
          {sanctions.csl_matches.length > 0 && (
            <div className="text-xs text-outline mt-0.5">
              {sanctions.csl_matches.length} Trade.gov CSL match(es)
            </div>
          )}
        </div>
        {extra && <div className="flex-1">{extra}</div>}
      </div>
    </div>
  );
}
