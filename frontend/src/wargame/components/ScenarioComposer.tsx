
/**
 * ScenarioComposer — left sidebar.
 * Controls: mode toggle (preset ↔ freeform), textarea, preset picker,
 *           Simulate button, playback controls, turn scrubber, status indicator.
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import { Terminal, Play, Square } from 'lucide-react';
import { Button } from './ui/Button';
import { PresetCard } from './PresetCard';
import { PlaybackControls } from './PlaybackControls';
import { TurnScrubber } from './TurnScrubber';
import { ModeToggle } from './ModeToggle';
import { FreeformPane } from './FreeformPane';
import { useSimStore } from '@/lib/store/simStore';
import { useSimStream } from '@/hooks/useSimStream';
import { createScenario, createSimulation } from '@/lib/api/client';
import { runTaiwanDemo } from '@/lib/demo/taiwanDemo';
import type { Domain } from '@/lib/types/sim-event';
import type { SeedEvent, SelectedCountry } from '@/lib/types/scenario';

const MAX_CHARS = 2000;

/**
 * DemoButton — plays the scripted Taiwan 2027 quarantine sequence locally.
 * Doesn't touch the backend, doesn't burn API tokens — pure frontend event
 * dispatch so the globe arcs/timeline/drawer all animate as if a real sim
 * were running. Useful for reliable demos and for verifying the viz when
 * the real sim is rate-limited.
 */
function DemoButton() {
  const cancelRef = useRef<(() => void) | null>(null);
  const [playing, setPlaying] = useState(false);

  // Cancel any pending dispatches on unmount so we don't fire into a stale store.
  useEffect(() => {
    return () => cancelRef.current?.();
  }, []);

  const handleClick = useCallback(() => {
    if (playing) {
      cancelRef.current?.();
      cancelRef.current = null;
      setPlaying(false);
      useSimStore.getState().setSimStatus('aborted');
      return;
    }
    cancelRef.current = runTaiwanDemo();
    setPlaying(true);
    // Auto-clear the playing flag when the sim transitions to completed.
    const unsub = useSimStore.subscribe((s) => {
      if (s.simStatus === 'completed' || s.simStatus === 'aborted') {
        setPlaying(false);
        unsub();
      }
    });
  }, [playing]);

  return (
    <Button
      variant="ghost"
      size="sm"
      className="w-full"
      onClick={handleClick}
    >
      {playing ? <Square size={12} /> : <Play size={12} />}
      {playing ? 'STOP DEMO' : 'PLAY DEMO (SCRIPTED)'}
    </Button>
  );
}

interface Preset {
  id: string;
  title: string;
  subtitle: string;
  domains: Domain[];
  description: string;
  country_ids: string[];
  max_turns: number;
  isComingSoon: boolean;
  /**
   * Structured events applied at turn 0 before any agent runs.  Authored to
   * match the `description` prose exactly — if the prose mentions a
   * quarantine, there MUST be a corresponding quarantine seed event, and
   * vice versa.  This is what makes "click preset → agents react" work:
   * without seed events the world starts flat and no_action dominates.
   */
  seed_events: SeedEvent[];
  /**
   * Optional baseline posture per country at turn 0.  Complements seed
   * events: a country can be "aggressive" without any specific action
   * having happened, which shapes how agents read the world.
   */
  posture_overrides?: Record<string, string>;
}

const PRESETS: Preset[] = [
  {
    id: 'taiwan-2027',
    title: 'China–Taiwan 2027',
    subtitle: '7 agents · 5 turns · ~40s',
    domains: ['kinetic_limited', 'cyber', 'economic'],
    description:
      'China announces a maritime quarantine of Taiwan\'s shipping lanes under the pretext of anti-smuggling enforcement. PLA Navy deploys Type 055 flotilla and coast-guard cutters; 7th Fleet surges to the Philippine Sea. Taiwan\'s semiconductor supply chain is at immediate risk. How do regional actors respond over 90 days?',
    country_ids: ['CHN', 'TWN', 'USA', 'JPN', 'KOR', 'PHL', 'AUS'],
    max_turns: 5,
    isComingSoon: false,
    posture_overrides: {
      CHN: 'aggressive',
      TWN: 'defensive',
      USA: 'deterrent',
      JPN: 'defensive',
      PHL: 'defensive',
    },
    seed_events: [
      {
        actor_country: 'CHN',
        target_country: 'TWN',
        domain: 'kinetic_limited',
        action_type: 'maritime_quarantine_declaration',
        escalation_rung: 3,
        rationale:
          "PRC announces a quarantine of Taiwan's shipping lanes under the pretext of anti-smuggling enforcement. Inciting event for the scenario.",
        payload: {
          area: 'Taiwan Strait + 200nm Taiwan EEZ',
          stated_justification: 'anti-smuggling customs enforcement',
          assets_deployed: [
            'Type 055 destroyer flotilla',
            'Type 052D destroyers',
            'Coast Guard cutters',
          ],
        },
      },
      {
        actor_country: 'USA',
        target_country: 'CHN',
        domain: 'diplomatic',
        action_type: 'condemnation_statement',
        escalation_rung: 1,
        rationale: 'Initial US verbal response — rhetorical, not kinetic.',
        payload: {
          message:
            'The United States condemns PRC coercion and reaffirms its commitment to a free and open Indo-Pacific.',
        },
      },
      {
        actor_country: 'USA',
        target_country: 'TWN',
        domain: 'kinetic_limited',
        action_type: 'carrier_group_surge',
        escalation_rung: 2,
        rationale:
          '7th Fleet repositioned to the Philippine Sea in response to the PLA quarantine.',
        payload: {
          asset: 'USS Ronald Reagan CSG',
          area: 'Philippine Sea',
          posture: 'deterrent',
        },
      },
      {
        actor_country: 'CHN',
        target_country: 'TWN',
        domain: 'cyber',
        action_type: 'infrastructure_probe',
        escalation_rung: 2,
        rationale:
          'PLASSF probes TWN power grid SCADA and TSMC OT networks — gray-zone reconnaissance consistent with quarantine preparation and supply-chain coercion.',
        payload: {
          target_systems: ['TWN national grid SCADA', 'TSMC fab OT network'],
          method: 'spearphishing + credential harvesting',
          attribution: 'PLASSF Network Systems Dept (inferred)',
        },
      },
    ],
  },
  {
    id: 'korean-peninsula',
    title: 'Korean Peninsula Crisis',
    subtitle: '6 agents · 5 turns · ~35s',
    domains: ['kinetic_limited', 'info', 'diplomatic'],
    description:
      'North Korea conducts a 7th underground nuclear test (claimed yield 150 kt) and announces a "sea-based Hwasan-19" ICBM test over the Sea of Japan within 30 days. Pyongyang demands lifting of sanctions and recognition as a nuclear state. US strategic bombers deploy to Osan; Seoul elevates Jindogae-3 readiness. How do Washington, Seoul, Tokyo, Beijing, and Moscow navigate the dual-track escalation?',
    country_ids: ['PRK', 'KOR', 'USA', 'JPN', 'CHN', 'RUS'],
    max_turns: 5,
    isComingSoon: false,
    posture_overrides: {
      PRK: 'aggressive',
      KOR: 'defensive',
      USA: 'deterrent',
      JPN: 'defensive',
    },
    seed_events: [
      {
        actor_country: 'PRK',
        target_country: null,
        domain: 'kinetic_limited',
        action_type: 'nuclear_test',
        escalation_rung: 4,
        rationale:
          "DPRK conducts its 7th underground nuclear test. Inciting event; sets the crisis clock.",
        payload: {
          test_number: 7,
          claimed_yield_kt: 150,
          site: 'Punggye-ri',
        },
      },
      {
        actor_country: 'PRK',
        target_country: 'JPN',
        domain: 'info',
        action_type: 'missile_test_announcement',
        escalation_rung: 2,
        rationale:
          "Pyongyang publicly commits to a sea-based Hwasan-19 ICBM test over the Sea of Japan within 30 days.",
        payload: {
          system: 'Hwasan-19',
          trajectory: 'Sea of Japan',
          window_days: 30,
        },
      },
      {
        actor_country: 'USA',
        target_country: 'KOR',
        domain: 'kinetic_limited',
        action_type: 'bomber_deployment',
        escalation_rung: 2,
        rationale:
          'B-1B/B-52 strategic bombers deploy to Osan Air Base in visible response.',
        payload: {
          airframe: 'B-1B Lancer',
          destination: 'Osan AB, ROK',
        },
      },
      {
        actor_country: 'KOR',
        target_country: null,
        domain: 'kinetic_limited',
        action_type: 'readiness_elevation',
        escalation_rung: 2,
        rationale: 'Seoul elevates Jindogae-3 (highest peacetime readiness).',
        payload: { level: 'Jindogae-3' },
      },
      {
        actor_country: 'PRK',
        target_country: 'KOR',
        domain: 'cyber',
        action_type: 'banking_intrusion_attempt',
        escalation_rung: 2,
        rationale:
          'Lazarus-group-attributed intrusion attempts against ROK financial institutions — DPRK foreign-currency generation under sanctions pressure, timed to crisis.',
        payload: {
          targets: ['Woori Bank', 'KEB Hana', 'Shinhan Bank'],
          group: 'Lazarus Group (APT38)',
          objective: 'USD extraction to fund missile program',
          indicators_of_compromise: ['C2 infra overlap with 2023 intrusion set'],
        },
      },
    ],
  },
  {
    id: 'south-china-sea',
    title: 'South China Sea Standoff',
    subtitle: '5 agents · 5 turns · ~30s',
    domains: ['kinetic_limited', 'diplomatic', 'economic'],
    description:
      'China unilaterally declares an Air Defense Identification Zone (ADIZ) covering the Spratly Islands and parts of Philippine EEZ. J-20 fighters scramble to intercept a Philippine P-3C maritime patrol; a US destroyer conducts a FONOP within 12 nm of Mischief Reef. BRP Sierra Madre resupply at Second Thomas Shoal is blocked by CCG water cannons. How do the parties negotiate between escalation and strategic patience?',
    country_ids: ['CHN', 'PHL', 'USA', 'JPN', 'AUS'],
    max_turns: 5,
    isComingSoon: false,
    posture_overrides: {
      CHN: 'aggressive',
      PHL: 'defensive',
      USA: 'deterrent',
    },
    seed_events: [
      {
        actor_country: 'CHN',
        target_country: 'PHL',
        domain: 'info',
        action_type: 'adiz_declaration',
        escalation_rung: 3,
        rationale:
          'PRC unilaterally declares a South China Sea ADIZ covering the Spratly Islands and parts of the Philippine EEZ. Inciting event.',
        payload: {
          area: 'Spratly Islands + PHL EEZ',
          type: 'Air Defense Identification Zone',
        },
      },
      {
        actor_country: 'CHN',
        target_country: 'PHL',
        domain: 'kinetic_limited',
        action_type: 'fighter_intercept',
        escalation_rung: 2,
        rationale:
          'PLAAF J-20s scramble to intercept a Philippine P-3C maritime patrol inside the newly declared ADIZ.',
        payload: {
          interceptor: 'J-20',
          intercepted: 'P-3C Orion',
          location: 'Spratly Islands',
        },
      },
      {
        actor_country: 'USA',
        target_country: 'CHN',
        domain: 'kinetic_limited',
        action_type: 'fonop',
        escalation_rung: 2,
        rationale:
          'US destroyer conducts a freedom-of-navigation operation within 12nm of Mischief Reef.',
        payload: {
          asset: 'Arleigh Burke DDG',
          feature: 'Mischief Reef',
          range_nm: 12,
        },
      },
      {
        actor_country: 'CHN',
        target_country: 'PHL',
        domain: 'kinetic_limited',
        action_type: 'ccg_water_cannon',
        escalation_rung: 2,
        rationale:
          'China Coast Guard blocks Philippine resupply of BRP Sierra Madre at Second Thomas Shoal using water cannons.',
        payload: {
          target_vessel: 'BRP Sierra Madre resupply',
          feature: 'Second Thomas Shoal',
          method: 'water cannon',
        },
      },
      {
        actor_country: 'CHN',
        target_country: 'PHL',
        domain: 'cyber',
        action_type: 'edca_reconnaissance',
        escalation_rung: 2,
        rationale:
          'PLA cyber teams probe Philippine C4I networks supporting EDCA sites — intelligence preparation for possible kinetic escalation in the ADIZ.',
        payload: {
          targets: ['AFP C4I network', 'EDCA site logistics'],
          method: 'supply-chain compromise + watering-hole',
          attribution: 'PLA Strategic Support Force (inferred)',
        },
      },
    ],
  },
];

const STATUS_DOT: Record<string, string> = {
  idle: 'bg-outline',
  pending: 'bg-economic animate-pulse',
  running: 'bg-cyber animate-pulse',
  paused: 'bg-economic',
  completed: 'bg-info',
  aborted: 'bg-kinetic',
  error: 'bg-kinetic animate-pulse',
};

/** Default title when the user composes a free-form scenario. */
const FREEFORM_TITLE = 'Custom Scenario';

/** Default max turns for free-form sims — matches preset cadence. */
const FREEFORM_MAX_TURNS = 5;

export function ScenarioComposer() {
  const [text, setText] = useState('');
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showConfirm, setShowConfirm] = useState(false);

  const simStatus = useSimStore((s) => s.simStatus);
  const currentTurn = useSimStore((s) => s.currentTurn);
  const maxTurns = useSimStore((s) => s.maxTurns);
  const currentSimId = useSimStore((s) => s.currentSimId);
  const events = useSimStore((s) => s.events);
  const scrubberTurn = useSimStore((s) => s.scrubberTurn);
  const composerMode = useSimStore((s) => s.composerMode);
  const setComposerMode = useSimStore((s) => s.setComposerMode);

  const setCurrentScenario = useSimStore((s) => s.setCurrentScenario);
  const setCurrentSimId = useSimStore((s) => s.setCurrentSimId);
  const setSimStatus = useSimStore((s) => s.setSimStatus);
  const setMaxTurns = useSimStore((s) => s.setMaxTurns);
  const setScrubberTurn = useSimStore((s) => s.setScrubberTurn);
  const reset = useSimStore((s) => s.reset);

  const { control } = useSimStream(currentSimId);

  const handlePresetClick = useCallback((preset: Preset) => {
    setSelectedPreset(preset.id);
    setText(preset.description);
    setError(null);
  }, []);

  /** Execute a preset-mode scenario. Byte-identical to the prior flow. */
  const doSimulatePreset = useCallback(async () => {
    if (!text.trim()) {
      setError('Please enter a scenario description.');
      return;
    }
    setIsSubmitting(true);
    setError(null);
    reset();
    setSimStatus('pending');

    try {
      const preset = PRESETS.find((p) => p.id === selectedPreset);
      const initial_conditions = preset
        ? {
            seed_events: preset.seed_events,
            posture_overrides: preset.posture_overrides ?? {},
          }
        : undefined;
      const scenario = await createScenario({
        title: preset?.title ?? FREEFORM_TITLE,
        description: text.trim(),
        country_ids: preset?.country_ids?.length
          ? preset.country_ids
          : ['CHN', 'TWN', 'USA', 'JPN', 'KOR', 'PHL', 'AUS', 'PRK', 'RUS', 'IND'],
        initial_conditions,
      });
      setCurrentScenario(scenario);

      const sim = await createSimulation({
        scenario_id: scenario.id,
        max_turns: preset?.max_turns ?? 5,
      });
      setMaxTurns(sim.max_turns);
      setCurrentSimId(sim.id);
      setSimStatus(sim.status);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to start simulation.';
      setError(msg);
      setSimStatus('error');
    } finally {
      setIsSubmitting(false);
    }
  }, [
    text, selectedPreset, reset, setSimStatus, setCurrentScenario,
    setCurrentSimId, setMaxTurns,
  ]);

  /** Execute a free-form-mode scenario, built from the extractor's output. */
  const doSimulateFreeform = useCallback(
    async (plan: {
      description: string;
      selectedCountries: SelectedCountry[];
      seedEvents: SeedEvent[];
      postureOverrides: Record<string, string>;
    }) => {
      setIsSubmitting(true);
      setError(null);
      reset();
      setSimStatus('pending');

      try {
        const countryIds = plan.selectedCountries.map((c) => c.iso3);
        // Backend requires ≥2 country_ids on ScenarioCreate. The extractor
        // guarantees 5 but we defend in depth.
        if (countryIds.length < 2) {
          throw new Error('At least 2 countries required to run a simulation.');
        }
        const scenario = await createScenario({
          title: FREEFORM_TITLE,
          description: plan.description,
          country_ids: countryIds,
          initial_conditions: {
            seed_events: plan.seedEvents,
            posture_overrides: plan.postureOverrides,
          },
        });
        setCurrentScenario(scenario);

        const sim = await createSimulation({
          scenario_id: scenario.id,
          max_turns: FREEFORM_MAX_TURNS,
        });
        setMaxTurns(sim.max_turns);
        setCurrentSimId(sim.id);
        setSimStatus(sim.status);
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to start simulation.';
        setError(msg);
        setSimStatus('error');
      } finally {
        setIsSubmitting(false);
      }
    },
    [reset, setSimStatus, setCurrentScenario, setCurrentSimId, setMaxTurns],
  );

  const handleSimulateClick = useCallback(() => {
    // Confirm only when a simulation is currently in flight; null (no sim yet),
    // 'completed', 'aborted', or 'error' all mean we can launch immediately.
    const inFlight =
      simStatus === 'running' ||
      simStatus === 'paused' ||
      simStatus === 'pending';
    if (inFlight) {
      setShowConfirm(true);
    } else {
      doSimulatePreset();
    }
  }, [simStatus, doSimulatePreset]);

  const completedTurns = Math.min(currentTurn, maxTurns);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 border-b border-outline-variant/30">
        <h1 className="font-mono font-black text-cyber text-lg uppercase tracking-tight">
          SCENARIO_COMPOSER
        </h1>
        <p className="font-mono text-[10px] text-on-surface-variant tracking-[0.2em]">
          V_0.1.0-ALPHA
        </p>
      </div>

      {/* Body (scrollable) */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-5">
        {/* Status row */}
        <div className="flex items-center gap-2">
          <span
            className={[
              'w-2 h-2 shrink-0',
              (simStatus && STATUS_DOT[simStatus]) || 'bg-outline',
            ].join(' ')}
          />
          <span className="font-mono text-[10px] text-on-surface-variant uppercase tracking-widest">
            {simStatus}
          </span>
          {(simStatus === 'running' || simStatus === 'paused' || simStatus === 'completed') && (
            <span className="font-mono text-[10px] text-cyber ml-auto">
              T{currentTurn}/{maxTurns} · {events.length} events
            </span>
          )}
        </div>

        {/* Mode toggle — switches between preset and free-form flows.
            Disabled while a simulation is in flight to avoid abandoning
            partially-built state. */}
        <ModeToggle
          mode={composerMode}
          onChange={setComposerMode}
          disabled={isSubmitting || simStatus === 'running'}
        />

        {composerMode === 'preset' ? (
          <>
            {/* Scenario textarea (preset mode — editable preset description) */}
            <div className="space-y-1.5">
              <label className="font-mono text-[10px] text-on-surface-variant uppercase tracking-widest">
                Scenario Initialization
              </label>
              <div className="relative">
                <textarea
                  value={text}
                  onChange={(e) => {
                    setText(e.target.value.slice(0, MAX_CHARS));
                    setError(null);
                  }}
                  placeholder="Select a preset below or switch to Free-form."
                  rows={5}
                  disabled={isSubmitting || simStatus === 'running'}
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
                <p className="font-mono text-[10px] text-kinetic">{error}</p>
              )}
            </div>

            {/* Preset cards */}
            <div className="space-y-2">
              <label className="font-mono text-[10px] text-on-surface-variant uppercase tracking-widest">
                Global Presets
              </label>
              {PRESETS.map((preset) => (
                <PresetCard
                  key={preset.id}
                  title={preset.title}
                  subtitle={preset.subtitle}
                  domains={preset.domains}
                  isComingSoon={preset.isComingSoon}
                  isSelected={selectedPreset === preset.id}
                  onClick={() => handlePresetClick(preset)}
                />
              ))}
            </div>
          </>
        ) : (
          <FreeformPane
            onExecute={doSimulateFreeform}
            isSubmitting={isSubmitting}
            disabled={simStatus === 'running'}
          />
        )}

        {/* Turn scrubber — only visible when there's a sim */}
        {completedTurns > 0 && (
          <TurnScrubber
            currentTurn={scrubberTurn || currentTurn}
            totalTurns={maxTurns}
            completedTurns={completedTurns}
            onChange={setScrubberTurn}
          />
        )}
      </div>

      {/* Footer */}
      <div className="px-4 py-3 space-y-3 border-t border-outline-variant/30">
        {/* Playback controls — only render once a sim has been started */}
        {simStatus !== null && (
          <PlaybackControls
            simStatus={simStatus}
            onPause={() => control('pause')}
            onResume={() => control('resume')}
            onAbort={() => control('abort')}
          />
        )}

        {/* Simulate button — only shown in preset mode. Free-form mode has
            its own Execute button inside AnalysisConfirmation so the user
            can see exactly what will launch. */}
        {composerMode === 'preset' && (
          <Button
            variant="primary"
            size="lg"
            className="w-full"
            loading={isSubmitting}
            disabled={isSubmitting || simStatus === 'running' || simStatus === 'pending'}
            onClick={handleSimulateClick}
          >
            <Terminal size={14} />
            EXECUTE SIMULATION
          </Button>
        )}

        {/* Demo button — offline scripted playback, no backend / LLM needed */}
        <DemoButton />
      </div>

      {/* Confirm-resimulate dialog */}
      {showConfirm && (
        <div className="absolute inset-0 z-50 bg-background/80 flex items-center justify-center p-6">
          <div className="w-full max-w-xs bg-surface-container-high border border-outline-variant p-5 space-y-4">
            <p className="font-mono text-xs text-on-surface">
              This will replace the current simulation. Continue?
            </p>
            <div className="flex gap-3">
              <Button
                variant="ghost"
                size="sm"
                className="flex-1"
                onClick={() => setShowConfirm(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                className="flex-1"
                onClick={() => {
                  setShowConfirm(false);
                  doSimulatePreset();
                }}
              >
                Simulate
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
