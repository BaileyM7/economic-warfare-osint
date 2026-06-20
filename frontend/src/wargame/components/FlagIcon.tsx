/**
 * FlagIcon — renders a country flag as inline SVG via `country-flag-icons`.
 *
 * Replaces the Unicode flag-emoji approach in places where the flag really
 * matters as identification. The Unicode emoji (`🇺🇸`) renders correctly on
 * macOS/iOS/Linux but falls back to bare regional-indicator letters on
 * Windows (e.g. "US"), which next to an explicit ISO3 label reads as the
 * country name twice.
 *
 * Tree-shakes per-country; total payload for the full 35-country pool
 * ≈ 18-22 KB and only imported flags are shipped to the browser.
 */

import type { ComponentType, SVGProps } from 'react';
import AE from 'country-flag-icons/react/3x2/AE';
import AR from 'country-flag-icons/react/3x2/AR';
import AU from 'country-flag-icons/react/3x2/AU';
import BR from 'country-flag-icons/react/3x2/BR';
import CA from 'country-flag-icons/react/3x2/CA';
import CN from 'country-flag-icons/react/3x2/CN';
import DE from 'country-flag-icons/react/3x2/DE';
import EG from 'country-flag-icons/react/3x2/EG';
import ET from 'country-flag-icons/react/3x2/ET';
import FR from 'country-flag-icons/react/3x2/FR';
import GB from 'country-flag-icons/react/3x2/GB';
import ID from 'country-flag-icons/react/3x2/ID';
import IL from 'country-flag-icons/react/3x2/IL';
import IN from 'country-flag-icons/react/3x2/IN';
import IR from 'country-flag-icons/react/3x2/IR';
import IT from 'country-flag-icons/react/3x2/IT';
import JP from 'country-flag-icons/react/3x2/JP';
import KP from 'country-flag-icons/react/3x2/KP';
import KR from 'country-flag-icons/react/3x2/KR';
import MX from 'country-flag-icons/react/3x2/MX';
import NG from 'country-flag-icons/react/3x2/NG';
import PH from 'country-flag-icons/react/3x2/PH';
import PK from 'country-flag-icons/react/3x2/PK';
import PL from 'country-flag-icons/react/3x2/PL';
import RU from 'country-flag-icons/react/3x2/RU';
import SA from 'country-flag-icons/react/3x2/SA';
import SG from 'country-flag-icons/react/3x2/SG';
import TH from 'country-flag-icons/react/3x2/TH';
import TR from 'country-flag-icons/react/3x2/TR';
import TW from 'country-flag-icons/react/3x2/TW';
import UA from 'country-flag-icons/react/3x2/UA';
import US from 'country-flag-icons/react/3x2/US';
import VE from 'country-flag-icons/react/3x2/VE';
import VN from 'country-flag-icons/react/3x2/VN';
import ZA from 'country-flag-icons/react/3x2/ZA';
import { ISO3_TO_ISO2, getCountryName } from '@/lib/geo';

type FlagComponent = ComponentType<SVGProps<SVGSVGElement> & { title?: string }>;

/**
 * Map of ISO-2 codes → SVG component. Covers the full 35-country pool.
 *
 * The `as unknown as` cast is a pragmatic workaround for a long-standing
 * type collision between React's `SVGSVGElement` and the
 * `country-flag-icons` types' `HTMLSVGElement`. The runtime values are
 * identical (React SVG components), but TypeScript sees two `FlagComponent`
 * nominal types. The alternative would be widening our own `FlagComponent`
 * to `ComponentType<any>`, which we'd lose the useful prop typing on.
 */
const FLAG_COMPONENTS: Record<string, FlagComponent> = {
  AE, AR, AU, BR, CA, CN, DE, EG, ET, FR, GB, ID, IL, IN, IR, IT,
  JP, KP, KR, MX, NG, PH, PK, PL, RU, SA, SG, TH, TR, TW, UA, US,
  VE, VN, ZA,
} as unknown as Record<string, FlagComponent>;

export interface FlagIconProps {
  /** ISO-3 country code, e.g. "USA". */
  iso3: string;
  /**
   * Tailwind class string controlling size + visual treatment. Defaults to
   * a clean rectangle. Override per call site (the AgentDrawer header wants
   * something larger than a decision-log row, for example).
   */
  className?: string;
  /** Optional accessible title; defaults to the full country name. */
  title?: string;
}

export function FlagIcon({
  iso3,
  className = 'w-5 h-3.5 ring-1 ring-outline-variant/40 shrink-0',
  title,
}: FlagIconProps) {
  const iso2 = ISO3_TO_ISO2[iso3];
  const Cmp = iso2 ? FLAG_COMPONENTS[iso2] : undefined;
  if (!Cmp) {
    // Unknown country — fall back to a small placeholder so layouts don't shift.
    return (
      <span
        className={[className, 'inline-block bg-outline-variant/30'].join(' ')}
        aria-label={iso3}
        title={title ?? iso3}
      />
    );
  }
  return <Cmp className={className} title={title ?? getCountryName(iso3)} />;
}
