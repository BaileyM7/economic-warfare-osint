import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import RiskFeedCard from '../components/RiskFeedCard';
import WatchlistManager from '../components/WatchlistManager';
import {
  fetchRiskFeed,
  refreshRiskFeed,
  prepareCoaPayload,
  generateCOAOptions,
  createCOA,
  type RiskFeedItem,
  type RiskFeedResponse,
} from '../api';
import type { BriefingSource } from '../types';

const CATEGORY_ORDER: { key: string; label: string; icon: string }[] = [
  { key: 'company_sanctions', label: 'Company Sanctions', icon: 'business' },
  { key: 'people_sanctions', label: 'People Sanctions', icon: 'person' },
  { key: 'markets', label: 'Markets', icon: 'show_chart' },
];

function formatTimestamp(iso: string | null): string {
  if (!iso) return 'never';
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

export default function RiskFeedPage() {
  const navigate = useNavigate();
  const [feed, setFeed] = useState<RiskFeedResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyItemId, setBusyItemId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const res = await fetchRiskFeed();
        if (active) setFeed(res);
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  async function handleRefresh() {
    setRefreshing(true);
    setError(null);
    try {
      const res = await refreshRiskFeed();
      setFeed(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRefreshing(false);
    }
  }

  async function handleRunCOA(item: RiskFeedItem) {
    setBusyItemId(item.id);
    setError(null);
    try {
      // Click-time enrichment: pull extra sources (GDELT articles, CSL/PEP/OFAC
      // cross-list checks, yfinance profile) so the COA prompt has >=2 distinct
      // citations to draw on. Best-effort — fall back to the raw card payload
      // if the endpoint errors.
      let analysisPayload: Record<string, unknown> = item.synthetic_payload;
      try {
        const prepared = await prepareCoaPayload(item.id);
        if (prepared && prepared.synthetic_payload) {
          analysisPayload = prepared.synthetic_payload;
        }
      } catch (enrichErr) {
        console.warn('prepare-coa enrichment failed; using raw payload:', enrichErr);
      }

      const objective = `Generate analyst-grade COAs in response to: ${item.headline}. Use the provided OFAC/GDELT/yfinance sources to justify each recommendation.`;
      const options = await generateCOAOptions({
        analysis_data: analysisPayload,
        objective,
      });

      const opt = Array.isArray(options) && options.length > 0 ? options[0] : {};
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const o = opt as any;

      const fallbackSources: BriefingSource[] =
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ((analysisPayload as any).sources_used as BriefingSource[] | undefined) ?? [];

      const created = await createCOA({
        name: o.name || `Risk Feed: ${item.entity}`,
        description: o.description || item.headline,
        target_entities:
          Array.isArray(o.target_entities) && o.target_entities.length > 0
            ? o.target_entities
            : [item.entity],
        action_type: o.action_type || 'sanction',
        recommendations: Array.isArray(o.recommendations) ? o.recommendations : [],
        friendly_fire: Array.isArray(o.friendly_fire) ? o.friendly_fire : [],
        expected_effects: Array.isArray(o.expected_effects) ? o.expected_effects : [],
        sources: Array.isArray(o.sources) && o.sources.length > 0 ? o.sources : fallbackSources,
        rationale: typeof o.rationale === 'string' ? o.rationale : '',
        confidence: typeof o.confidence === 'number' ? o.confidence : null,
        status: 'draft',
      });
      navigate(`/coa?focus=${created.id}`);
    } catch (err) {
      console.error('Risk feed COA generation failed:', err);
      setError(err instanceof Error ? err.message : 'COA generation failed');
    } finally {
      setBusyItemId(null);
    }
  }

  const itemsByCategory = useMemo(() => {
    const out: Record<string, RiskFeedItem[]> = {};
    for (const cat of CATEGORY_ORDER) out[cat.key] = [];
    for (const it of feed?.items ?? []) {
      if (out[it.category]) out[it.category].push(it);
      else (out['markets'] ??= []).push(it);
    }
    return out;
  }, [feed]);

  const lastRefreshSource = feed?.last_refresh?.source ?? '—';
  const lastRefreshAt = formatTimestamp(feed?.last_refresh?.at ?? null);
  const totalCount = feed?.items?.length ?? 0;

  return (
    <div className="p-6">
      <header className="mb-6">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl font-bold text-on-surface flex items-center gap-2">
              <span className="material-symbols-outlined text-primary">dashboard</span>
              Risk Feed
            </h1>
            <p className="text-xs text-on-surface-variant mt-1">
              Proactive surfacing of risk shifts. Click any card to translate into a Course of
              Action.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <div className="text-[11px] text-outline text-right">
              <div>
                Source:{' '}
                <span className="font-mono text-on-surface-variant">{lastRefreshSource}</span>
              </div>
              <div>
                Last refresh:{' '}
                <span className="font-mono text-on-surface-variant">{lastRefreshAt}</span>
              </div>
              <div>
                Items: <span className="font-mono text-on-surface-variant">{totalCount}</span>
              </div>
            </div>
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshing}
              data-vn="feed-refresh"
              className="bg-primary text-on-primary px-4 py-2 rounded-md text-sm font-semibold flex items-center gap-2 hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity"
            >
              <span
                className={`material-symbols-outlined text-base ${refreshing ? 'animate-spin' : ''}`}
              >
                {refreshing ? 'progress_activity' : 'refresh'}
              </span>
              {refreshing ? 'Refreshing…' : 'Refresh feed'}
            </button>
          </div>
        </div>

        {error && (
          <div className="mt-4 bg-error/10 border border-error/30 text-error rounded-md px-4 py-2 text-sm">
            {error}
          </div>
        )}

        {feed?.last_refresh?.errors && feed.last_refresh.errors.length > 0 && (
          <div className="mt-4 bg-tertiary/10 border border-tertiary/30 text-tertiary rounded-md px-4 py-2 text-xs">
            Some sources failed during refresh:
            <ul className="mt-1 ml-4 list-disc">
              {feed.last_refresh.errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          </div>
        )}
      </header>

      <WatchlistManager onChanged={handleRefresh} />

      {loading ? (
        <div className="text-on-surface-variant text-sm">Loading risk feed…</div>
      ) : totalCount === 0 ? (
        <div className="bg-surface-container-low border border-outline-variant/10 rounded-lg p-8 text-center">
          <span className="material-symbols-outlined text-4xl text-outline mb-2">inbox</span>
          <p className="text-on-surface-variant text-sm mb-4">
            No risk items yet. Click <span className="font-semibold">Refresh feed</span> to pull the
            latest from OFAC, GDELT, and market data.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {CATEGORY_ORDER.map((cat) => {
            const items = itemsByCategory[cat.key] ?? [];
            return (
              <section key={cat.key} className="flex flex-col">
                <div className="flex items-center justify-between mb-3 px-1">
                  <h2 className="text-sm font-bold uppercase tracking-wider text-on-surface flex items-center gap-2">
                    <span className="material-symbols-outlined text-base text-primary">
                      {cat.icon}
                    </span>
                    {cat.label}
                  </h2>
                  <span className="text-[11px] text-outline font-mono">{items.length}</span>
                </div>
                <div className="space-y-3">
                  {items.length === 0 ? (
                    <div className="bg-surface-container-low border border-outline-variant/10 border-dashed rounded-lg p-4 text-[11px] text-outline text-center">
                      No items in this category.
                    </div>
                  ) : (
                    items.map((it) => (
                      <RiskFeedCard
                        key={it.id}
                        item={it}
                        busy={busyItemId === it.id}
                        onAction={handleRunCOA}
                      />
                    ))
                  )}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
