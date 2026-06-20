/**
 * Country types — mirrors the `countries` table schema in architecture.md.
 */

export interface CountryProfile {
  population?: number;
  military_strength_index?: number;
  [key: string]: unknown;
}

export interface CountryDoctrine {
  risk_tolerance?: string;
  time_horizon?: string;
  priority?: string[];
  [key: string]: unknown;
}

export interface CountryMilitaryAssets {
  carrier_groups?: number;
  submarines?: number;
  cyber_units?: string[];
  [key: string]: unknown;
}

/** Full country object returned by /api/countries and /api/countries/{iso3}. */
export interface Country {
  id: string;
  iso3: string;
  name: string;
  gdp_usd: number;
  profile: CountryProfile;
  doctrine: CountryDoctrine;
  red_lines: string[];
  military_assets: CountryMilitaryAssets;
  updated_at: string;
}

export interface CountryList {
  items: Country[];
  total: number;
  limit: number;
  offset: number;
}

/** The 10 vertical-slice ISO-3 codes. */
export const SLICE_COUNTRIES = [
  'CHN', 'TWN', 'USA', 'JPN', 'KOR',
  'PHL', 'AUS', 'PRK', 'RUS', 'IND',
] as const;

export type SliceCountryIso = (typeof SLICE_COUNTRIES)[number];
