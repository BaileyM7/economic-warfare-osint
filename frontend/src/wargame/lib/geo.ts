/**
 * Country ISO-3 → geographic centroid (lat, lon) lookup + ISO3/ISO2 maps +
 * display names. Values mirror src/shared/seeds/countries.yaml so the
 * frontend can render any pool country the backend returns (e.g. from the
 * free-form extractor) without a round-trip.
 *
 * Keep in sync with countries.yaml if that file changes.
 */

export interface LatLon {
  lat: number;
  lon: number;
}

/** Geographic centroids for the 35 pool countries. */
export const COUNTRY_CENTROIDS: Record<string, LatLon> = {
  CHN: { lat: 35.86, lon: 104.19 },
  TWN: { lat: 23.69, lon: 120.96 },
  USA: { lat: 37.09, lon: -95.71 },
  JPN: { lat: 36.2, lon: 138.25 },
  KOR: { lat: 35.91, lon: 127.77 },
  PHL: { lat: 12.88, lon: 121.77 },
  AUS: { lat: -25.27, lon: 133.78 },
  PRK: { lat: 40.34, lon: 127.51 },
  RUS: { lat: 61.52, lon: 105.32 },
  IND: { lat: 20.59, lon: 78.96 },
  DEU: { lat: 51.17, lon: 10.45 },
  FRA: { lat: 46.23, lon: 2.21 },
  GBR: { lat: 55.38, lon: -3.44 },
  POL: { lat: 51.92, lon: 19.15 },
  UKR: { lat: 48.38, lon: 31.17 },
  TUR: { lat: 38.96, lon: 35.24 },
  ITA: { lat: 41.87, lon: 12.57 },
  ISR: { lat: 31.05, lon: 34.85 },
  IRN: { lat: 32.43, lon: 53.69 },
  SAU: { lat: 23.89, lon: 45.08 },
  EGY: { lat: 26.82, lon: 30.8 },
  ARE: { lat: 23.42, lon: 53.85 },
  BRA: { lat: -14.24, lon: -51.93 },
  MEX: { lat: 23.63, lon: -102.55 },
  CAN: { lat: 56.13, lon: -106.35 },
  VEN: { lat: 6.42, lon: -66.59 },
  ARG: { lat: -38.42, lon: -63.62 },
  VNM: { lat: 14.06, lon: 108.28 },
  IDN: { lat: -0.79, lon: 113.92 },
  THA: { lat: 15.87, lon: 100.99 },
  PAK: { lat: 30.38, lon: 69.35 },
  SGP: { lat: 1.35, lon: 103.82 },
  NGA: { lat: 9.08, lon: 8.68 },
  ZAF: { lat: -30.56, lon: 22.94 },
  ETH: { lat: 9.15, lon: 40.49 },
};

/** Returns the centroid for a given ISO-3 code, or null if not found. */
export function getCentroid(iso3: string): LatLon | null {
  return COUNTRY_CENTROIDS[iso3] ?? null;
}

/** Flag emoji for ISO-3 country codes. */
export const COUNTRY_FLAGS: Record<string, string> = {
  CHN: '🇨🇳', TWN: '🇹🇼', USA: '🇺🇸', JPN: '🇯🇵', KOR: '🇰🇷',
  PHL: '🇵🇭', AUS: '🇦🇺', PRK: '🇰🇵', RUS: '🇷🇺', IND: '🇮🇳',
  DEU: '🇩🇪', FRA: '🇫🇷', GBR: '🇬🇧', POL: '🇵🇱', UKR: '🇺🇦',
  TUR: '🇹🇷', ITA: '🇮🇹', ISR: '🇮🇱', IRN: '🇮🇷', SAU: '🇸🇦',
  EGY: '🇪🇬', ARE: '🇦🇪', BRA: '🇧🇷', MEX: '🇲🇽', CAN: '🇨🇦',
  VEN: '🇻🇪', ARG: '🇦🇷', VNM: '🇻🇳', IDN: '🇮🇩', THA: '🇹🇭',
  PAK: '🇵🇰', SGP: '🇸🇬', NGA: '🇳🇬', ZAF: '🇿🇦', ETH: '🇪🇹',
};

export function getFlag(iso3: string): string {
  return COUNTRY_FLAGS[iso3] ?? '🏴';
}

/**
 * ISO-3 → ISO-2 mapping for the 35 pool countries.
 * Required because `country-flag-icons` (used by FlagIcon) is keyed on the
 * ISO 3166-1 alpha-2 code, while the rest of the codebase is alpha-3.
 */
export const ISO3_TO_ISO2: Record<string, string> = {
  CHN: 'CN', TWN: 'TW', USA: 'US', JPN: 'JP', KOR: 'KR',
  PHL: 'PH', AUS: 'AU', PRK: 'KP', RUS: 'RU', IND: 'IN',
  DEU: 'DE', FRA: 'FR', GBR: 'GB', POL: 'PL', UKR: 'UA',
  TUR: 'TR', ITA: 'IT', ISR: 'IL', IRN: 'IR', SAU: 'SA',
  EGY: 'EG', ARE: 'AE', BRA: 'BR', MEX: 'MX', CAN: 'CA',
  VEN: 'VE', ARG: 'AR', VNM: 'VN', IDN: 'ID', THA: 'TH',
  PAK: 'PK', SGP: 'SG', NGA: 'NG', ZAF: 'ZA', ETH: 'ET',
};

/** Full display names for the 35 countries. */
export const COUNTRY_NAMES: Record<string, string> = {
  CHN: 'China', TWN: 'Taiwan', USA: 'United States', JPN: 'Japan',
  KOR: 'South Korea', PHL: 'Philippines', AUS: 'Australia',
  PRK: 'North Korea', RUS: 'Russia', IND: 'India',
  DEU: 'Germany', FRA: 'France', GBR: 'United Kingdom', POL: 'Poland',
  UKR: 'Ukraine', TUR: 'Türkiye', ITA: 'Italy',
  ISR: 'Israel', IRN: 'Iran', SAU: 'Saudi Arabia', EGY: 'Egypt',
  ARE: 'United Arab Emirates',
  BRA: 'Brazil', MEX: 'Mexico', CAN: 'Canada', VEN: 'Venezuela',
  ARG: 'Argentina',
  VNM: 'Vietnam', IDN: 'Indonesia', THA: 'Thailand', PAK: 'Pakistan',
  SGP: 'Singapore',
  NGA: 'Nigeria', ZAF: 'South Africa', ETH: 'Ethiopia',
};

export function getCountryName(iso3: string): string {
  return COUNTRY_NAMES[iso3] ?? iso3;
}

/** All 35 ISO-3 codes in the canonical country pool. */
export const ALL_POOL_ISO3 = Object.keys(COUNTRY_NAMES);
