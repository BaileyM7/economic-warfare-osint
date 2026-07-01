import type { RiskFeedItem } from '../api';
import { cardProvenance } from '../lib/cardProvenance';
import AddToWatchlistButton from './AddToWatchlistButton';

const PROVENANCE_CHIP: Record<'global' | 'watchlist', { label: string; className: string }> = {
  global: {
    label: 'Global',
    className: 'bg-outline/10 text-outline border-outline/30',
  },
  watchlist: {
    label: 'Watchlist',
    className: 'bg-primary/15 text-primary border-primary/30',
  },
};

function ProvenanceBadge({ itemId }: { itemId: string }) {
  const prov = cardProvenance(itemId);
  if (!prov) return null;
  const { label, className } = PROVENANCE_CHIP[prov];
  return (
    <span
      className={`px-2 py-0.5 rounded-full text-[10px] font-semibold border whitespace-nowrap uppercase tracking-wider ${className}`}
    >
      {label}
    </span>
  );
}

function formatEventDate(iso: string | null): string | null {
  if (!iso) return null;
  // Accept either YYYY-MM-DD or full ISO timestamp; treat as UTC date.
  const dateOnly = iso.length >= 10 ? iso.slice(0, 10) : iso;
  const d = new Date(`${dateOnly}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

function formatFetchedAgo(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diffMs = Date.now() - d.getTime();
  const sec = Math.max(0, Math.round(diffMs / 1000));
  if (sec < 60) return `fetched ${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `fetched ${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `fetched ${hr}h ago`;
  const day = Math.round(hr / 24);
  return `fetched ${day}d ago`;
}

const SEVERITY_BORDER: Record<string, string> = {
  high: 'border-l-error',
  medium: 'border-l-tertiary',
  low: 'border-l-primary',
  info: 'border-l-outline',
};

const SEVERITY_CHIP: Record<string, string> = {
  high: 'bg-error/15 text-error border-error/30',
  medium: 'bg-tertiary/15 text-tertiary border-tertiary/30',
  low: 'bg-primary/15 text-primary border-primary/30',
  info: 'bg-secondary/15 text-secondary border-secondary/30',
};

const CATEGORY_ICON: Record<string, string> = {
  company_sanctions: 'business',
  people_sanctions: 'person',
  markets: 'show_chart',
};

interface Props {
  item: RiskFeedItem;
  busy: boolean;
  onAction: (item: RiskFeedItem) => void;
}

export default function RiskFeedCard({ item, busy, onAction }: Props) {
  const border = SEVERITY_BORDER[item.severity] ?? SEVERITY_BORDER.info;
  const chip = SEVERITY_CHIP[item.severity] ?? SEVERITY_CHIP.info;
  const icon = CATEGORY_ICON[item.category] ?? 'flag';
  const eventDate = formatEventDate(item.event_at);
  const fetchedAgo = formatFetchedAgo(item.fetched_at);

  return (
    <div
      className={`bg-surface-container-low border border-outline-variant/10 border-l-2 ${border} rounded-lg p-4 transition-all hover:bg-surface-container hover:shadow-md`}
    >
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="material-symbols-outlined text-sm text-on-surface-variant">{icon}</span>
          <span className="text-[10px] uppercase tracking-widest text-outline">
            {item.category.replace(/_/g, ' ')}
          </span>
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {item.priority_weight != null && (
            <span
              className="px-2 py-0.5 rounded-full text-[10px] font-semibold border border-primary/40 bg-primary-container/30 text-primary whitespace-nowrap uppercase tracking-wider flex items-center gap-1"
              title={`Boosted by a team collection priority (weight ${item.priority_weight})`}
            >
              <span className="material-symbols-outlined text-[11px]">push_pin</span>
              Priority
            </span>
          )}
          <ProvenanceBadge itemId={item.id} />
          <span
            className={`px-2 py-0.5 rounded-full text-[10px] font-semibold border whitespace-nowrap uppercase tracking-wider ${chip}`}
          >
            {item.severity}
          </span>
        </div>
      </div>

      <h4 className="text-sm font-semibold text-on-surface mb-2 leading-snug">{item.headline}</h4>

      <p className="text-xs text-on-surface-variant leading-relaxed mb-2 line-clamp-2">
        {item.entity}
      </p>

      <div className="flex items-center gap-2 mb-3 text-[10px] text-outline">
        {eventDate ? (
          <>
            <span className="material-symbols-outlined text-[12px]">event</span>
            <time dateTime={item.event_at ?? undefined} className="text-on-surface-variant">
              {eventDate}
            </time>
            <span className="opacity-60">·</span>
            <span className="opacity-80">{fetchedAgo}</span>
          </>
        ) : (
          <>
            <span className="material-symbols-outlined text-[12px]">schedule</span>
            <span className="text-on-surface-variant">{fetchedAgo}</span>
          </>
        )}
      </div>

      <div className="flex items-center justify-between gap-2">
        <a
          href={item.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[11px] text-primary hover:underline flex items-center gap-1 truncate"
          onClick={(e) => e.stopPropagation()}
        >
          <span className="material-symbols-outlined text-xs">open_in_new</span>
          source
        </a>
        <div className="flex items-center gap-2">
          <AddToWatchlistButton item={item} />
          <button
            type="button"
            onClick={() => onAction(item)}
            disabled={busy}
            className="text-[11px] font-semibold text-primary hover:bg-primary/10 px-3 py-1.5 rounded-md border border-primary/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
          >
            {busy ? (
              <>
                <span className="material-symbols-outlined text-xs animate-spin">
                  progress_activity
                </span>
                Generating COA…
              </>
            ) : (
              <>
                Run COA
                <span className="material-symbols-outlined text-xs">arrow_forward</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
