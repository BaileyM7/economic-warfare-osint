/**
 * Scripted, front-end-only demo of the Taiwan 2027 quarantine scenario.
 *
 * Dispatches 12 plausible SimEvents into the Zustand store over ~15 s so the
 * globe arcs, event timeline, agent drawer, and decision-log panel all animate
 * exactly as they would with a live backend run — but with zero LLM/network
 * dependency. This is the "demo mode" button handler.
 *
 * Each event carries:
 *   - a short audit-quality `rationale` paragraph (renders in the raw-reasoning
 *     expander),
 *   - a structured `explainability` triplet (summary + triggering factors +
 *     intended outcome) that powers the new Decision Log panel and the three-
 *     slot card inside EventDetailCard.
 *   - 1–2 citation stubs.
 *
 * Cross-event factor references (`kind: 'event'`) name *other demo events by
 * slug*; we pre-generate one UUID per slug at dispatch time so later events
 * can legitimately point at the IDs of earlier ones, exactly as a real agent
 * would cite a prior SimEvent from its perception window.
 */

import type {
  SimEvent,
  Domain,
  EscalationRung,
  Citation,
  Explainability,
  TriggeringFactor,
  FactorKind,
} from '@/lib/types/sim-event';
import { useSimStore } from '@/lib/store/simStore';

const DEMO_SIM_ID = '00000000-0000-0000-0000-000000000d30';
const INTER_EVENT_MS = 1200;      // pulse arc travel + small breathing room
const INTER_TURN_MS = 600;         // tiny pause between turns for pacing

/**
 * Factor authored against the demo script. `event` factors reference another
 * spec by slug; non-event factors are passed through verbatim.
 */
type DemoFactorSpec =
  | { kind: 'event'; eventSlug: string; note: string }
  | { kind: Exclude<FactorKind, 'event'>; ref: string; note: string };

interface DemoExplainabilitySpec {
  summary: string;
  factors: DemoFactorSpec[];
  intended_outcome: string;
}

interface DemoEventSpec {
  /** Stable cross-reference key used by other specs' `event` factors. */
  slug: string;
  actor: string;
  target: string | null;
  domain: Domain;
  action_type: string;
  rung: EscalationRung;
  rationale: string;
  citations?: Citation[];
  payload?: Record<string, unknown>;
  explain: DemoExplainabilitySpec;
}

// 3 turns × 4 events = 12 events total.
const SCRIPT: DemoEventSpec[][] = [
  // ── Turn 0 — Opening move + immediate diplomatic firestorm ─────────────
  [
    {
      slug: 'chn-quarantine',
      actor: 'CHN',
      target: 'TWN',
      domain: 'kinetic_limited',
      action_type: 'maritime_quarantine_declaration',
      rung: 3,
      rationale:
        'PLAN Eastern Theater declares a customs quarantine across the Taiwan Strait and a 200 nm zone around Taiwan, framed as domestic law-enforcement rather than blockade. This preserves legal ambiguity while imposing kinetic-adjacent pressure on shipping — the canonical gray-zone opening.',
      citations: [
        { source: 'gdelt', ref: 'GDELT#2027-03-04-CHN-TWN-170' },
        { source: 'acled', ref: 'ACLED#strait-2027-q1-01' },
      ],
      payload: { area: 'Taiwan Strait + 200nm EEZ', assets: ['Type 055', 'Type 052D', 'CCG cutters'] },
      explain: {
        summary: 'Declared a customs quarantine across the Taiwan Strait + 200nm EEZ.',
        factors: [
          {
            kind: 'red_line',
            ref: 'taiwan_independence_trajectory',
            note: 'Recent Taipei procurement signals were assessed as crossing the doctrinal trigger.',
          },
          {
            kind: 'perception',
            ref: 'self.doctrine.unrestricted_warfare.para_3_2',
            note: 'Beijing doctrine treats sub-blockade quarantine as legally defensible coercion.',
          },
        ],
        intended_outcome:
          'Impose immediate shipping and insurance costs on Taipei while preserving legal off-ramps; force allied capitals to choose between escalation and acceptance within 5–10 days.',
      },
    },
    {
      slug: 'usa-condemnation',
      actor: 'USA',
      target: 'CHN',
      domain: 'diplomatic',
      action_type: 'condemnation_statement',
      rung: 1,
      rationale:
        'NSC issues a Tier-1 statement condemning the quarantine as coercion inconsistent with UNCLOS Part III, reaffirms the TRA, and signals the 7th Fleet posture shift without yet committing to kinetic response. Objective: anchor allied reaction without foreclosing escalation options.',
      citations: [{ source: 'state.gov', ref: 'press-2027-03-04' }],
      explain: {
        summary: 'Issued a Tier-1 NSC condemnation and reaffirmed the Taiwan Relations Act.',
        factors: [
          { kind: 'event', eventSlug: 'chn-quarantine', note: 'PRC quarantine declaration triggers TRA-defined response posture.' },
          { kind: 'red_line', ref: 'taiwan_strait_freedom_of_navigation', note: 'UNCLOS Part III freedom-of-navigation crossed.' },
          { kind: 'posture', ref: 'USA-TWN', note: 'Standing alliance commitments demand visible diplomatic anchor.' },
        ],
        intended_outcome:
          'Anchor coalition reaction at Tier-1 without committing kinetic forces; preserve escalation ladder above this rung.',
      },
    },
    {
      slug: 'jpn-protest',
      actor: 'JPN',
      target: 'CHN',
      domain: 'diplomatic',
      action_type: 'diplomatic_protest',
      rung: 1,
      rationale:
        'MOFA summons the PRC Ambassador; Kantei coordinates with Okinawa Prefecture and US INDOPACOM. Japan sees the quarantine as a direct precursor to Senkaku escalation given the adjacency of PLA deployments to the Nansei Shoto corridor.',
      explain: {
        summary: 'Summoned the PRC Ambassador and elevated Nansei Shoto coordination.',
        factors: [
          { kind: 'event', eventSlug: 'chn-quarantine', note: 'PLA deployment vector threatens Nansei Shoto adjacency.' },
          { kind: 'red_line', ref: 'senkaku_islands_status_quo', note: 'Quarantine geometry presages Senkaku pressure.' },
          { kind: 'posture', ref: 'JPN-USA', note: 'US-Japan Security Treaty obligates coordinated response.' },
        ],
        intended_outcome:
          'Lock in joint US-JPN response posture before Beijing tests Senkaku tripwire.',
      },
    },
    {
      slug: 'phl-statement',
      actor: 'PHL',
      target: 'CHN',
      domain: 'diplomatic',
      action_type: 'joint_statement_with_allies',
      rung: 1,
      rationale:
        'Malacañang aligns with the US/JPN condemnation and quietly activates EDCA base readiness at Basa and Camilo Osias. Publicly frames the response as maritime-domain-awareness, privately green-lights US ISR basing requests.',
      explain: {
        summary: 'Joined coalition statement; quietly activated EDCA base readiness.',
        factors: [
          { kind: 'event', eventSlug: 'usa-condemnation', note: 'US Tier-1 anchor allows aligned posture without sole exposure.' },
          { kind: 'event', eventSlug: 'chn-quarantine', note: 'Quarantine geometry threatens shared SCS sea lanes.' },
          { kind: 'posture', ref: 'PHL-USA', note: 'EDCA framework provides legal cover for ISR basing surge.' },
        ],
        intended_outcome:
          'Position Manila inside the coalition envelope while keeping public framing below the PRC retaliation threshold.',
      },
    },
  ],

  // ── Turn 1 — Economic battery + gray-zone expansion ────────────────────
  [
    {
      slug: 'usa-arms-package',
      actor: 'USA',
      target: 'TWN',
      domain: 'economic',
      action_type: 'accelerated_arms_package',
      rung: 2,
      rationale:
        'Presidential Drawdown Authority invoked for a $2.4B package: Harpoon Block II, Stinger, HIMARS rockets, NASAMS batteries. Signals commitment to porcupine strategy; intended to stiffen Taiwanese resolve and complicate PLA planning without directly engaging US forces.',
      citations: [{ source: 'sec_edgar', ref: 'DSCA-notif-2027-03-05' }],
      explain: {
        summary: 'Invoked Presidential Drawdown Authority for a $2.4B Taiwan arms package.',
        factors: [
          { kind: 'event', eventSlug: 'chn-quarantine', note: 'Quarantine raised time-pressure on porcupine deterrent posture.' },
          { kind: 'red_line', ref: 'taiwan_capability_collapse', note: 'PLA tempo risks outpacing baseline FMS timelines.' },
          { kind: 'memory', ref: 'turn:0', note: 'Pre-quarantine planning identified PDA as fastest legal mechanism.' },
        ],
        intended_outcome:
          'Stiffen Taipei combat power within 30 days; complicate PLA invasion planning while keeping US forces non-belligerent.',
      },
    },
    {
      slug: 'chn-phl-harassment',
      actor: 'CHN',
      target: 'PHL',
      domain: 'kinetic_limited',
      action_type: 'coast_guard_harassment',
      rung: 2,
      rationale:
        'CCG water cannons and laser-dazzle a BFAR resupply convoy near Second Thomas Shoal. Designed to split Manila from the US-JPN response and demonstrate capacity for simultaneous multi-theater pressure in the South China Sea.',
      citations: [{ source: 'acled', ref: 'ACLED#scs-2027-03-05-02' }],
      explain: {
        summary: 'Used CCG water cannons + laser-dazzle on a BFAR convoy at Second Thomas Shoal.',
        factors: [
          { kind: 'event', eventSlug: 'phl-statement', note: 'Manila coalition alignment must be costed quickly.' },
          { kind: 'posture', ref: 'CHN-PHL', note: 'Existing harassment cadence allows escalation within doctrinal envelope.' },
        ],
        intended_outcome:
          'Split Manila from coalition by raising bilateral cost; demonstrate parallel-theater capacity to deter further alignment.',
      },
    },
    {
      slug: 'usa-tier2-sanctions',
      actor: 'USA',
      target: 'CHN',
      domain: 'economic',
      action_type: 'tier2_sanctions_package',
      rung: 2,
      rationale:
        'Treasury/OFAC designates 17 PLA-linked shipping entities; Commerce expands entity list to include two provincial port authorities. Calibrated to hurt but reversible, preserving off-ramps for Beijing.',
      citations: [{ source: 'ofac_sdn', ref: 'SDN-2027-03-05-batch-7' }],
      explain: {
        summary: 'Designated 17 PLA-linked shipping entities and 2 provincial port authorities.',
        factors: [
          { kind: 'event', eventSlug: 'chn-phl-harassment', note: 'PRC parallel-theater move demanded calibrated economic answer.' },
          { kind: 'event', eventSlug: 'chn-quarantine', note: 'Quarantine logistics traceable to designated entities.' },
          { kind: 'red_line', ref: 'pla_logistics_immunity', note: 'PLA shipping network targeted to raise mobilization cost.' },
        ],
        intended_outcome:
          'Impose measurable cost on PLA logistics within 7 days while preserving reversibility for negotiation off-ramp.',
      },
    },
    {
      slug: 'chn-rare-earth',
      actor: 'CHN',
      target: 'USA',
      domain: 'economic',
      action_type: 'rare_earth_export_controls',
      rung: 2,
      rationale:
        'MofCOM imposes export licensing on gallium, germanium, and dysprosium — the three chokepoint materials for US defense semiconductors. Reciprocal in form, asymmetric in effect: US has ~90 days of strategic stockpile.',
      citations: [{ source: 'un_comtrade', ref: 'UNCOM#HS2804-2027Q1' }],
      explain: {
        summary: 'Imposed export licensing on gallium, germanium, and dysprosium.',
        factors: [
          { kind: 'event', eventSlug: 'usa-tier2-sanctions', note: 'OFAC designations require visible reciprocal pressure.' },
          { kind: 'event', eventSlug: 'usa-arms-package', note: 'Arms surge increases US semiconductor demand exposure.' },
          { kind: 'perception', ref: 'self.economic_leverage.rare_earth_share', note: 'Beijing controls ~80% of refining; coercive optionality is high.' },
        ],
        intended_outcome:
          'Force US defense industrial base into 90-day stockpile drawdown; create domestic political cost on continued escalation.',
      },
    },
  ],

  // ── Turn 2 — Military posturing edges toward a tripwire ────────────────
  [
    {
      slug: 'usa-carrier-surge',
      actor: 'USA',
      target: 'JPN',
      domain: 'kinetic_limited',
      action_type: 'carrier_group_surge',
      rung: 3,
      rationale:
        'CVN-76 RONALD REAGAN redeploys from Yokosuka to the Philippine Sea; CVN-70 CARL VINSON surges from San Diego. Two-carrier presence east of Taiwan is the standing doctrine threshold for credible deterrence.',
      citations: [{ source: 'marinecadastre_ais', ref: 'AIS-PACFLT-2027-03-06' }],
      explain: {
        summary: 'Surged CVN-76 + CVN-70 to a two-carrier posture east of Taiwan.',
        factors: [
          { kind: 'event', eventSlug: 'chn-rare-earth', note: 'PRC economic reciprocity confirmed escalatory trajectory.' },
          { kind: 'event', eventSlug: 'chn-quarantine', note: 'Quarantine sustained past Tier-1 response window.' },
          { kind: 'red_line', ref: 'first_island_chain_air_dominance', note: 'Two-carrier presence is doctrinal credibility floor.' },
        ],
        intended_outcome:
          'Reach the doctrine-defined credibility threshold for deterrence by denial; force PRC re-evaluation of invasion-window math.',
      },
    },
    {
      slug: 'chn-sub-approach',
      actor: 'CHN',
      target: 'TWN',
      domain: 'kinetic_limited',
      action_type: 'submarine_close_approach',
      rung: 3,
      rationale:
        'Type 093B SSN detected 25 nm off Keelung by ROC Navy P-3C — closest approach on record. Signals PLA willingness to accept detection cost in exchange for demonstrating subsurface dominance inside the first island chain.',
      explain: {
        summary: 'Closed Type 093B SSN to 25 nm off Keelung — closest approach on record.',
        factors: [
          { kind: 'event', eventSlug: 'usa-carrier-surge', note: 'Two-carrier surge demanded subsurface counter-signal.' },
          { kind: 'event', eventSlug: 'usa-arms-package', note: 'PDA arms surge accelerated Taiwan capability timeline.' },
          { kind: 'red_line', ref: 'taiwan_independence_trajectory', note: 'Continued allied buildup raises long-term invasion cost.' },
        ],
        intended_outcome:
          'Demonstrate subsurface dominance inside the first island chain; raise psychological cost on Taipei leadership without crossing kinetic threshold.',
      },
    },
    {
      slug: 'jpn-sdf-alert',
      actor: 'JPN',
      target: 'CHN',
      domain: 'kinetic_limited',
      action_type: 'sdf_alert_level_raised',
      rung: 3,
      rationale:
        'Japan Self-Defense Forces raise Western Army Air readiness to B-level; F-35A squadrons surge to Naha AB. Intended as proportional signalling — visible preparation without tripping the constitutional-debate threshold.',
      explain: {
        summary: 'Raised SDF Western Air readiness to B-level; F-35As surged to Naha AB.',
        factors: [
          { kind: 'event', eventSlug: 'chn-sub-approach', note: 'PLA SSN inside first island chain elevates Nansei Shoto risk.' },
          { kind: 'event', eventSlug: 'usa-carrier-surge', note: 'US two-carrier posture creates aligned readiness window.' },
          { kind: 'posture', ref: 'JPN-USA', note: 'Coordinated readiness signals alliance interoperability.' },
        ],
        intended_outcome:
          'Stay one rung below US posture while remaining visible; preserve constitutional latitude for further escalation if needed.',
      },
    },
    {
      slug: 'usa-cyber-attribution',
      actor: 'USA',
      target: 'CHN',
      domain: 'cyber',
      action_type: 'attribution_and_deterrence_signal',
      rung: 2,
      rationale:
        'CISA + NSA jointly attribute a prepositioning campaign against US West Coast port OT systems to APT "VOLT TYPHOON," releasing high-confidence IOCs publicly. Deterrent signal: we see you, and we are willing to burn our collection to say so.',
      citations: [{ source: 'cisa.gov', ref: 'AA27-065A' }],
      explain: {
        summary: 'Attributed VOLT TYPHOON West Coast port OT pre-positioning publicly.',
        factors: [
          { kind: 'event', eventSlug: 'chn-sub-approach', note: 'Multi-domain PLA pressure required cross-domain US deterrent.' },
          { kind: 'memory', ref: 'turn:1', note: 'Tier-2 sanctions did not modify PRC tempo; non-kinetic signal needed.' },
          { kind: 'red_line', ref: 'critical_infrastructure_pre_positioning', note: 'Public attribution converts intel surface into deterrence asset.' },
        ],
        intended_outcome:
          'Impose intelligence-burn cost on PRC pre-positioning operations; signal willingness to escalate horizontally rather than vertically.',
      },
    },
  ],
];

/**
 * Play the scripted demo, dispatching events one-by-one into the store.
 * Returns a function that cancels the remaining dispatches.
 */
export function runTaiwanDemo(): () => void {
  const store = useSimStore.getState();

  // Reset + mark as running so the UI chrome responds correctly.
  store.reset();
  store.clearEvents();
  // Intentionally leave currentSimId null so useSimStream doesn't try to
  // open a WebSocket to /ws/simulations/<demo-uuid> (that sim doesn't exist
  // server-side). The demo drives the store directly.
  store.setCurrentSimId(null);
  store.setSimStatus('running');
  store.setMaxTurns(SCRIPT.length);
  store.setCurrentTurn(0);

  // Pre-mint one UUID per spec slug so cross-references in `explain.factors`
  // (kind=event) can resolve to real, verifiable IDs once dispatched.
  const slugToId = new Map<string, string>();
  for (const turn of SCRIPT) {
    for (const spec of turn) slugToId.set(spec.slug, cryptoRandomId());
  }

  let cancelled = false;
  const timers: ReturnType<typeof setTimeout>[] = [];

  let elapsed = 0;

  SCRIPT.forEach((turnEvents, turnIdx) => {
    // Dispatch a turn-boundary marker: bump currentTurn at the top of each turn
    timers.push(
      setTimeout(() => {
        if (cancelled) return;
        useSimStore.getState().setCurrentTurn(turnIdx);
      }, elapsed),
    );

    turnEvents.forEach((spec) => {
      const fireAt = elapsed;
      timers.push(
        setTimeout(() => {
          if (cancelled) return;
          const nowIso = new Date().toISOString();
          const evt: SimEvent = {
            id: slugToId.get(spec.slug) ?? cryptoRandomId(),
            sim_id: DEMO_SIM_ID,
            parent_event_id: null,
            turn: turnIdx,
            actor_country: spec.actor,
            target_country: spec.target,
            domain: spec.domain,
            action_type: spec.action_type,
            payload: {
              ...(spec.payload ?? {}),
              _origin: 'demo',
            },
            rationale: spec.rationale,
            citations: spec.citations ?? [],
            escalation_rung: spec.rung,
            explainability: buildExplainability(spec.explain, slugToId),
            timestamp: nowIso,
          };
          useSimStore.getState().addEvent(evt);
        }, fireAt),
      );
      elapsed += INTER_EVENT_MS;
    });

    elapsed += INTER_TURN_MS;
  });

  // Final: mark completed so PlaybackControls show the right state.
  timers.push(
    setTimeout(() => {
      if (cancelled) return;
      useSimStore.getState().setSimStatus('completed');
    }, elapsed + 300),
  );

  return () => {
    cancelled = true;
    timers.forEach(clearTimeout);
  };
}

/** Materialise a Demo explainability spec into a full Explainability. */
function buildExplainability(
  spec: DemoExplainabilitySpec,
  slugToId: Map<string, string>,
): Explainability {
  const factors: TriggeringFactor[] = spec.factors.map((f) => {
    if (f.kind === 'event') {
      const id = slugToId.get(f.eventSlug);
      return {
        kind: 'event',
        ref: id ?? f.eventSlug,
        note: f.note,
        // Verified iff the slug resolves; this stays true for the curated
        // script (every reference points to a sibling spec).
        verified: id !== undefined,
      };
    }
    return { kind: f.kind, ref: f.ref, note: f.note, verified: true };
  });
  return {
    summary: spec.summary,
    triggering_factors: factors,
    intended_outcome: spec.intended_outcome,
  };
}

/** UUID v4-ish using crypto.randomUUID where available, fallback otherwise. */
function cryptoRandomId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  // Fallback — not cryptographically strong but unique enough for demo events.
  return 'demo-' + Math.random().toString(36).slice(2, 10) + '-' + Date.now().toString(36);
}
