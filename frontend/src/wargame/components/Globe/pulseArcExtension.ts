/**
 * pulseArcExtension.ts
 *
 * Custom Deck.gl LayerExtension that makes each arc *grow* from origin to
 * destination, then stay statically drawn. Replaces the earlier cycling
 * "pulse head" behavior — users read the growth as the action flowing from
 * actor country to target country.
 *
 * Mechanism:
 *   - Per-arc `instanceArcStartTime` (seconds-since-page-load) passed via
 *     an instanced attribute fed by the `getArcStartTime` accessor.
 *   - `pulseArc.time` uniform (also seconds-since-page-load, unwrapped)
 *     updated each draw via `setShaderModuleProps`.
 *   - Fragment shader computes `progress = clamp((time - start) / grow, 0, 1)`
 *     and discards fragments where the arc's 0→1 position exceeds progress,
 *     producing a clean origin→target reveal.
 *   - Once progress hits 1.0 the arc stays drawn at its normal color; the
 *     existing turn-relative alpha fade in useGlobeData handles aging.
 *
 * Precision note: time here is seconds since page load, staying well under
 * 10^5 for any plausible session, so float32 in the shader is fine. The
 * previous implementation had to wrap Date.now()/1000 in JS because
 * ~1.78e9 * pulseSpeed blew through float32 precision; that concern no
 * longer applies.
 */

import { LayerExtension } from '@deck.gl/core';

const uniformBlock = /* glsl */ `\
uniform pulseArcUniforms {
  float time;
  float growSeconds;
} pulseArc;
`;

const pulseArcModule = {
  name: 'pulseArc',
  vs: uniformBlock,
  fs: uniformBlock,
  uniformTypes: {
    time: 'f32',
    growSeconds: 'f32',
  },
} as const;

export interface PulseArcProps {
  /** Seconds for an arc to fully draw from origin to destination. Default 1.5. */
  growSeconds?: number;
  /** Per-arc accessor returning seconds-since-page-load when the arc was born. */
  getArcStartTime?: number | ((d: unknown) => number);
}

export class PulseArcExtension extends LayerExtension<PulseArcProps> {
  static defaultProps = {
    growSeconds: { type: 'number', value: 1.5, min: 0.1 },
    getArcStartTime: { type: 'accessor', value: 0 },
  };
  static extensionName = 'PulseArcExtension';

  getShaders() {
    return {
      modules: [pulseArcModule],
      inject: {
        'vs:#decl': /* glsl */ `
          in float instanceArcStartTime;
          out float v_pulse_t;
          out float v_arc_start_time;
        `,
        'vs:#main-end': /* glsl */ `
          v_pulse_t = geometry.uv.x;
          v_arc_start_time = instanceArcStartTime;
        `,

        'fs:#decl': /* glsl */ `
          in float v_pulse_t;
          in float v_arc_start_time;
        `,
        // Grow the arc from origin (t=0) to destination (t=1) over
        // growSeconds. Beyond that, the arc stays fully drawn at its normal
        // color. Gate on picking.isActive so the picking pass renders the
        // full geometry — otherwise the arc becomes progressively un-pickable
        // during growth.
        'fs:DECKGL_FILTER_COLOR': /* glsl */ `
          if (picking.isActive < 0.5) {
            float elapsed = pulseArc.time - v_arc_start_time;
            float progress = clamp(elapsed / max(pulseArc.growSeconds, 0.0001), 0.0, 1.0);
            if (v_pulse_t > progress) {
              discard;
            }
          }
        `,
      },
    };
  }

  initializeState(this: unknown, _context: unknown, _extension: PulseArcExtension) {
    const layer = this as {
      getAttributeManager: () => {
        addInstanced: (attrs: Record<string, unknown>) => void;
      } | null;
    };
    const attributeManager = layer.getAttributeManager();
    if (!attributeManager) return; // sub-layers without an attribute manager

    attributeManager.addInstanced({
      instanceArcStartTime: {
        size: 1,
        type: 'float32',
        accessor: 'getArcStartTime',
        defaultValue: 0,
      },
    });
  }

  draw(this: unknown, _params: unknown, _extension: PulseArcExtension) {
    const layer = this as {
      props: PulseArcProps;
      setShaderModuleProps: (props: Record<string, unknown>) => void;
    };
    const growSeconds = layer.props.growSeconds ?? 1.5;
    const time = performance.now() / 1000;
    layer.setShaderModuleProps({ pulseArc: { time, growSeconds } });
  }
}
