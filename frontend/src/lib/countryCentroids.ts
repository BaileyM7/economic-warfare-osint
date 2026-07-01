/* Approximate country centroids (lat, lon) for the geographic graph view (#38).
 * Keyed by ISO-3166 alpha-2 (what GLEIF / most sources emit); a small alias table
 * maps common full names to codes. Not exhaustive — entities whose country can't
 * be resolved are listed as "unlocated" in the map view rather than mis-placed. */

export interface LatLon {
  lat: number
  lon: number
}

export const COUNTRY_CENTROIDS: Record<string, LatLon> = {
  US: { lat: 39.0, lon: -98.0 },
  CN: { lat: 35.0, lon: 104.0 },
  HK: { lat: 22.3, lon: 114.2 },
  TW: { lat: 23.7, lon: 121.0 },
  RU: { lat: 58.0, lon: 60.0 },
  DE: { lat: 51.2, lon: 10.4 },
  FR: { lat: 46.6, lon: 2.4 },
  GB: { lat: 54.0, lon: -2.0 },
  NL: { lat: 52.1, lon: 5.3 },
  DK: { lat: 56.0, lon: 10.0 },
  SE: { lat: 62.0, lon: 15.0 },
  CH: { lat: 46.8, lon: 8.2 },
  IT: { lat: 42.8, lon: 12.6 },
  ES: { lat: 40.0, lon: -3.7 },
  JP: { lat: 36.2, lon: 138.3 },
  KR: { lat: 36.5, lon: 127.8 },
  KP: { lat: 40.3, lon: 127.5 },
  IN: { lat: 22.0, lon: 79.0 },
  SG: { lat: 1.35, lon: 103.8 },
  AE: { lat: 24.0, lon: 54.0 },
  SA: { lat: 24.0, lon: 45.0 },
  IR: { lat: 32.0, lon: 53.0 },
  IQ: { lat: 33.2, lon: 43.7 },
  TR: { lat: 39.0, lon: 35.2 },
  IL: { lat: 31.5, lon: 34.8 },
  UA: { lat: 49.0, lon: 32.0 },
  BY: { lat: 53.7, lon: 27.9 },
  PK: { lat: 30.4, lon: 69.3 },
  BR: { lat: -10.0, lon: -52.0 },
  MX: { lat: 23.6, lon: -102.5 },
  CA: { lat: 56.0, lon: -106.0 },
  AU: { lat: -25.0, lon: 133.0 },
  ZA: { lat: -29.0, lon: 24.0 },
  NG: { lat: 9.1, lon: 8.7 },
  EG: { lat: 26.8, lon: 30.8 },
  VE: { lat: 6.4, lon: -66.6 },
  CU: { lat: 21.5, lon: -79.5 },
  SY: { lat: 35.0, lon: 38.0 },
  KZ: { lat: 48.0, lon: 68.0 },
  MY: { lat: 4.2, lon: 101.9 },
  ID: { lat: -2.5, lon: 118.0 },
  TH: { lat: 15.0, lon: 101.0 },
  VN: { lat: 16.0, lon: 108.0 },
  PA: { lat: 8.5, lon: -80.0 },
  KY: { lat: 19.3, lon: -81.3 }, // Cayman Islands (offshore)
  VG: { lat: 18.4, lon: -64.6 }, // British Virgin Islands
  LU: { lat: 49.8, lon: 6.1 },
}

const NAME_ALIASES: Record<string, string> = {
  'united states': 'US',
  usa: 'US',
  america: 'US',
  china: 'CN',
  'hong kong': 'HK',
  taiwan: 'TW',
  russia: 'RU',
  'russian federation': 'RU',
  germany: 'DE',
  france: 'FR',
  'united kingdom': 'GB',
  uk: 'GB',
  britain: 'GB',
  netherlands: 'NL',
  denmark: 'DK',
  sweden: 'SE',
  switzerland: 'CH',
  japan: 'JP',
  'south korea': 'KR',
  'north korea': 'KP',
  india: 'IN',
  singapore: 'SG',
  'united arab emirates': 'AE',
  uae: 'AE',
  iran: 'IR',
  turkey: 'TR',
  ukraine: 'UA',
  belarus: 'BY',
  'cayman islands': 'KY',
  luxembourg: 'LU',
}

/** Resolve a raw country string (ISO2 or name) to a centroid, or null. */
export function resolveCentroid(raw: string | null | undefined): LatLon | null {
  if (!raw) return null
  const s = raw.trim()
  if (!s) return null
  const upper = s.toUpperCase()
  if (upper.length === 2 && COUNTRY_CENTROIDS[upper]) return COUNTRY_CENTROIDS[upper]
  const code = NAME_ALIASES[s.toLowerCase()]
  if (code && COUNTRY_CENTROIDS[code]) return COUNTRY_CENTROIDS[code]
  if (COUNTRY_CENTROIDS[upper]) return COUNTRY_CENTROIDS[upper]
  return null
}
