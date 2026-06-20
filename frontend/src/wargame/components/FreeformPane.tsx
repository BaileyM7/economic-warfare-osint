
/**
 * FreeformPane — free-form scenario composition surface.
 *
 * Flow:
 *   1. User types prose into the textarea and clicks Analyze.
 *   2. We call /api/scenarios/extract-events, which returns seed events +
 *      top-5 countries + provenance source.
 *   3. AnalysisConfirmation renders the result with removable chips / events.
 *   4. User clicks Execute. FreeformPane delegates to the parent
 *      `onExecute(plan)` callback which owns scenario + simulation creation.
 *
 * Invariants:
 *   - Keeps a local `text` buffer so typing is fluid; only commits to the
 *     store on Analyze success (via setExtractionResult) and on Execute.
 *   - Discarding wipes the extraction result + edits; re-Analyze is free.
 */

import { useCallback, useState } from 'react';
import { Loader2, Search } from 'lucide-react';
import { Button } from './ui/Button';
import { AnalysisConfirmation } from './AnalysisConfirmation';
import { useSimStore } from '@/lib/store/simStore';
import { extractScenarioEvents, ApiRequestError } from '@/lib/api/client';
import type {
  ExtractEventsResponse,
  SeedEvent,
  SelectedCountry,
} from '@/lib/types/scenario';

const MAX_CHARS = 4000;

export interface FreeformPaneProps {
  /** Called when the user clicks Execute with the confirmed plan. */
  onExecute: (plan: {
    description: string;
    selectedCountries: SelectedCountry[];
    seedEvents: SeedEvent[];
    postureOverrides: Record<string, string>;
  }) => Promise<void> | void;
  /** Disable inputs while a simulation is submitting. */
  isSubmitting?: boolean;
  /** Disable inputs while a simulation is in flight. */
  disabled?: boolean;
}

export function FreeformPane({
  onExecute,
  isSubmitting = false,
  disabled = false,
}: FreeformPaneProps) {
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);

  const extractionResult = useSimStore((s) => s.extractionResult);
  const setExtractionResult = useSimStore((s) => s.setExtractionResult);
  const editedSelectedCountries = useSimStore((s) => s.editedSelectedCountries);
  const editedSeedEvents = useSimStore((s) => s.editedSeedEvents);
  const setEditedSelectedCountries = useSimStore((s) => s.setEditedSelectedCountries);
  const setEditedSeedEvents = useSimStore((s) => s.setEditedSeedEvents);

  // Derive the current working copy of countries/events from the store.
  // Priority: user edits > extraction result > empty.
  const currentCountries = deriveCountries(extractionResult, editedSelectedCountries);
  const currentEvents = deriveEvents(extractionResult, editedSeedEvents);

  const handleAnalyze = useCallback(async () => {
    const trimmed = text.trim();
    if (trimmed.length < 10) {
      setError('Describe the scenario in at least a full sentence.');
      return;
    }
    setIsAnalyzing(true);
    setError(null);
    try {
      const result = await extractScenarioEvents({ description: trimmed });
      setExtractionResult(result);
    } catch (err) {
      const msg = extractErrorMessage(err);
      setError(msg);
    } finally {
      setIsAnalyzing(false);
    }
  }, [text, setExtractionResult]);

  const handleSelectedCountriesChange = useCallback(
    (countries: SelectedCountry[]) => {
      setEditedSelectedCountries(countries.map((c) => c.iso3));
      // Keep a parallel record of the user's current-view objects so the
      // confirmation card renders consistently across re-mounts. Simpler
      // than re-hydrating from extractionResult + edits on every render.
      setEditedSelectedCountriesFull(countries);
    },
    [setEditedSelectedCountries],
  );

  const handleSeedEventsChange = useCallback(
    (events: SeedEvent[]) => {
      setEditedSeedEvents(events);
    },
    [setEditedSeedEvents],
  );

  const handleDiscard = useCallback(() => {
    setExtractionResult(null);
    setError(null);
    clearEditedFullCache();
  }, [setExtractionResult]);

  const handleExecute = useCallback(async () => {
    if (!extractionResult) return;
    const trimmed = text.trim();
    await onExecute({
      description: trimmed,
      selectedCountries: currentCountries,
      seedEvents: currentEvents,
      postureOverrides: extractionResult.posture_overrides,
    });
  }, [extractionResult, text, currentCountries, currentEvents, onExecute]);

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label
          htmlFor="freeform-prompt"
          className="font-mono text-[10px] text-on-surface-variant uppercase tracking-widest"
        >
          Scenario Prompt
        </label>
        <div className="relative">
          <textarea
            id="freeform-prompt"
            value={text}
            onChange={(e) => {
              setText(e.target.value.slice(0, MAX_CHARS));
              setError(null);
            }}
            placeholder="Iran launches drone strikes on Saudi oil infrastructure after Israeli strikes on Hezbollah command nodes in Beirut."
            rows={5}
            disabled={disabled || isAnalyzing || isSubmitting}
            className={[
              'w-full bg-surface-container-lowest',
              'border-0 border-t border-cyber/60',
              'p-3 font-mono text-xs text-on-surface',
              'focus:outline-none focus:border-cyber',
              'placeholder:text-on-surface-variant/40',
              'resize-none transition-colors',
              'disabled:opacity-50 disabled:cursor-not-allowed',
            ].join(' ')}
          />
          <span className="absolute bottom-2 right-2 font-mono text-[10px] text-on-surface-variant/50">
            {text.length}/{MAX_CHARS}
          </span>
        </div>
        {error && (
          <p className="font-mono text-[10px] text-kinetic" role="alert">
            {error}
          </p>
        )}
      </div>

      {/* Analyze button — hidden after a successful extraction; the
          confirmation card drives the next step. Re-Analyze requires
          Discard first so we never silently overwrite user edits. */}
      {!extractionResult && (
        <Button
          variant="ghost"
          size="sm"
          className="w-full"
          onClick={handleAnalyze}
          disabled={disabled || isAnalyzing || isSubmitting || text.trim().length < 10}
          loading={isAnalyzing}
        >
          {isAnalyzing ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />}
          {isAnalyzing ? 'Analyzing…' : 'Analyze'}
        </Button>
      )}

      {extractionResult && (
        <AnalysisConfirmation
          result={extractionResult}
          selectedCountries={currentCountries}
          onSelectedCountriesChange={handleSelectedCountriesChange}
          seedEvents={currentEvents}
          onSeedEventsChange={handleSeedEventsChange}
          onExecute={handleExecute}
          onDiscard={handleDiscard}
          isSubmitting={isSubmitting}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Small module-level cache: when the user removes a country, we lose the
// relevance_score + rationale from the extraction result (it was keyed by
// iso3 inside selected_countries). Rather than pulling "full" SelectedCountry
// objects through Zustand (which would need a second state field), we cache
// the last authoritative list here. Parent-level wipe via handleDiscard keeps
// it honest.
let _editedFullCache: SelectedCountry[] | null = null;

function setEditedSelectedCountriesFull(list: SelectedCountry[]): void {
  _editedFullCache = list;
}

function clearEditedFullCache(): void {
  _editedFullCache = null;
}

function deriveCountries(
  result: ExtractEventsResponse | null,
  edits: string[] | null,
): SelectedCountry[] {
  if (!result) return [];
  if (edits === null) return result.selected_countries;
  // Reconstitute SelectedCountry objects, preferring the cached full list
  // if the isos match, else falling back to the original extraction entries.
  const cacheByIso = new Map((_editedFullCache ?? []).map((c) => [c.iso3, c]));
  const originalByIso = new Map(
    result.selected_countries.map((c) => [c.iso3, c]),
  );
  return edits.map(
    (iso): SelectedCountry =>
      cacheByIso.get(iso) ??
      originalByIso.get(iso) ?? {
        iso3: iso,
        relevance_score: 0.5,
        rationale: 'Manually added by user.',
      },
  );
}

function deriveEvents(
  result: ExtractEventsResponse | null,
  edits: SeedEvent[] | null,
): SeedEvent[] {
  if (!result) return [];
  return edits ?? result.seed_events;
}

function extractErrorMessage(err: unknown): string {
  if (err instanceof ApiRequestError) {
    // Backend returns 422 with ExtractionFailedError's message via the
    // envelope; surface it verbatim so the user sees why.
    return err.message || 'Extraction failed.';
  }
  if (err instanceof Error) return err.message;
  return 'Failed to analyze scenario.';
}
