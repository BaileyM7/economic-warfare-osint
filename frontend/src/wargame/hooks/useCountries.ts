
/**
 * SWR hook for fetching the country registry.
 * Caches for 5 minutes — countries don't change during a session.
 */

import useSWR from 'swr';
import { listCountries } from '@/lib/api/client';
import type { Country } from '@/lib/types/country';

const FIVE_MINUTES = 5 * 60 * 1000;

export interface UseCountriesResult {
  countries: Country[];
  isLoading: boolean;
  error: Error | undefined;
}

export function useCountries(): UseCountriesResult {
  const { data, isLoading, error } = useSWR(
    '/api/countries',
    () => listCountries({ limit: 200 }),
    { dedupingInterval: FIVE_MINUTES },
  );

  return {
    countries: data?.items ?? [],
    isLoading,
    error: error as Error | undefined,
  };
}

export interface UseCountryResult {
  country: Country | undefined;
  isLoading: boolean;
  error: Error | undefined;
}

export function useCountry(iso3: string | null): UseCountryResult {
  const { countries, isLoading, error } = useCountries();
  return {
    country: iso3 ? countries.find((c) => c.iso3 === iso3) : undefined,
    isLoading,
    error,
  };
}
