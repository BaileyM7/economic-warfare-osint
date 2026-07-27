import { useEffect, useRef, useState, type RefObject } from 'react';
import { getToken } from '../../api';

/** Where "Launch app" goes: signed-in visitors skip the login screen. */
export function launchTarget(): string {
  return getToken() ? '/risk-feed' : '/login';
}

/**
 * Landing-page-local hooks. The landing chunk is self-contained by design —
 * see frontend/CLAUDE.md on lazy route chunks.
 */

/** True once the element has entered the viewport (unobserves after first hit
 *  when `once`, otherwise tracks continuously). */
export function useInView<T extends Element>(
  ref: RefObject<T | null>,
  { threshold = 0.2, once = true }: { threshold?: number; once?: boolean } = {},
): boolean {
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === 'undefined') {
      setInView(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setInView(true);
          if (once) observer.disconnect();
        } else if (!once) {
          setInView(false);
        }
      },
      { threshold },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, threshold, once]);

  return inView;
}

export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  );

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  return reduced;
}

/** Shared rAF-throttled window scroll position (used by the nav backdrop). */
export function useScrolledPast(px: number): boolean {
  const [past, setPast] = useState(() => typeof window !== 'undefined' && window.scrollY > px);
  const ticking = useRef(false);

  useEffect(() => {
    const onScroll = () => {
      if (ticking.current) return;
      ticking.current = true;
      requestAnimationFrame(() => {
        setPast(window.scrollY > px);
        ticking.current = false;
      });
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener('scroll', onScroll);
  }, [px]);

  return past;
}
