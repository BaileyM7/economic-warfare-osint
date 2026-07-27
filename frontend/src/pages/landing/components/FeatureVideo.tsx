import { useEffect, useRef, useState } from 'react';
import { useInView, usePrefersReducedMotion } from '../hooks';

interface FeatureVideoProps {
  /** Optional — while demo clips are unrecorded, the poster renders alone. */
  src?: string;
  poster?: string;
  label: string;
}

/**
 * Hairline-framed demo clip. `preload="none"` + play-on-visibility keeps
 * video bytes off the wire until the block scrolls into view (no CDN in
 * front of the Render dyno — see docs/06). Under prefers-reduced-motion the
 * clip never autoplays; a play control is shown instead.
 */
export default function FeatureVideo({ src, poster, label }: FeatureVideoProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const visible = useInView(wrapRef, { threshold: 0.5, once: false });
  const reducedMotion = usePrefersReducedMotion();
  const [userStarted, setUserStarted] = useState(false);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !src) return;
    if (reducedMotion && !userStarted) return;
    if (visible) {
      void video.play().catch(() => undefined);
    } else {
      video.pause();
    }
  }, [visible, src, reducedMotion, userStarted]);

  return (
    <div
      ref={wrapRef}
      className="relative border border-outline-variant bg-surface-container-low aspect-video overflow-hidden"
    >
      {src ? (
        <>
          <video
            ref={videoRef}
            src={src}
            poster={poster}
            muted
            loop
            playsInline
            preload="none"
            aria-label={label}
            className="absolute inset-0 h-full w-full object-cover"
          />
          {reducedMotion && !userStarted && (
            <button
              type="button"
              onClick={() => setUserStarted(true)}
              aria-label={`Play ${label} demo`}
              className="absolute inset-0 flex items-center justify-center bg-background/40 hover:bg-background/20 transition-colors"
            >
              <span className="flex h-14 w-14 items-center justify-center border border-outline-variant bg-surface-container">
                <svg viewBox="0 0 16 16" width="18" height="18" aria-hidden="true">
                  <polygon points="5,3 13,8 5,13" fill="currentColor" className="text-on-surface" />
                </svg>
              </span>
            </button>
          )}
        </>
      ) : poster ? (
        <img
          src={poster}
          alt={`${label} interface`}
          loading="lazy"
          className="absolute inset-0 h-full w-full object-cover object-top"
        />
      ) : (
        <div className="tactical-grid absolute inset-0 flex items-center justify-center">
          <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-outline">
            {label} — demo forthcoming
          </span>
        </div>
      )}
      <div className="pointer-events-none absolute left-0 top-0 border-b border-r border-outline-variant bg-background/80 px-2.5 py-1">
        <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-outline">
          {label}
        </span>
      </div>
    </div>
  );
}
