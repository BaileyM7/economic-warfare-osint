import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useInView, usePrefersReducedMotion } from '../hooks';

interface FeatureVideoProps {
  /** Optional — while demo clips are unrecorded, the poster renders alone. */
  src?: string;
  poster?: string;
  label: string;
}

const ExpandGlyph = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
    <path
      d="M9.5 2.5h4v4M13.5 2.5 9 7M6.5 13.5h-4v-4M2.5 13.5 7 9"
      stroke="currentColor"
      fill="none"
      strokeWidth="1.5"
    />
  </svg>
);

const CloseGlyph = () => (
  <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
    <path d="M3 3l10 10M13 3 3 13" stroke="currentColor" fill="none" strokeWidth="1.5" />
  </svg>
);

/** Near-fullscreen overlay for a clip: dimmed backdrop, Esc / click-out / ✕ to
 *  close. Portaled to <body> so ancestor transforms can't trap the fixed layer. */
function Lightbox({ src, label, onClose }: { src: string; label: string; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    // Lock page scroll while open.
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    closeRef.current?.focus();
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`${label} demo, expanded`}
      className="fixed inset-0 z-[100] flex items-center justify-center bg-background/90 p-4 sm:p-10"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-[1600px] border border-outline-variant bg-surface-container-low"
        style={{ maxHeight: '88vh', aspectRatio: '16 / 9' }}
        onClick={(e) => e.stopPropagation()}
      >
        <video
          src={src}
          muted
          loop
          playsInline
          autoPlay
          controls
          aria-label={label}
          className="h-full w-full object-contain"
        />
        <div className="pointer-events-none absolute left-0 top-0 border-b border-r border-outline-variant bg-background/80 px-2.5 py-1">
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-outline">
            {label}
          </span>
        </div>
        <button
          ref={closeRef}
          type="button"
          onClick={onClose}
          aria-label="Close expanded video"
          className="absolute right-0 top-0 flex h-9 w-9 items-center justify-center border-b border-l border-outline-variant bg-background/80 text-on-surface-variant transition-colors hover:bg-surface-container hover:text-on-surface"
        >
          <CloseGlyph />
        </button>
      </div>
    </div>,
    document.body,
  );
}

/**
 * Hairline-framed demo clip. `preload="none"` + play-on-visibility keeps
 * video bytes off the wire until the block scrolls into view (no CDN in
 * front of the Render dyno — see docs/06). Under prefers-reduced-motion the
 * clip never autoplays inline; a play control is shown instead. Clicking the
 * clip (or its expand glyph) opens a near-fullscreen lightbox — clips are
 * encoded at 1920w for exactly this.
 */
export default function FeatureVideo({ src, poster, label }: FeatureVideoProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const visible = useInView(wrapRef, { threshold: 0.5, once: false });
  const reducedMotion = usePrefersReducedMotion();
  const [userStarted, setUserStarted] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !src) return;
    // Inline clip pauses while the lightbox is open (or offscreen).
    if (expanded || !visible || (reducedMotion && !userStarted)) {
      video.pause();
      return;
    }
    void video.play().catch(() => undefined);
  }, [visible, src, reducedMotion, userStarted, expanded]);

  const close = useCallback(() => setExpanded(false), []);

  return (
    <div
      ref={wrapRef}
      className="group relative aspect-video overflow-hidden border border-outline-variant bg-surface-container-low"
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
            className="absolute inset-0 h-full w-full cursor-zoom-in object-cover"
            onClick={() => setExpanded(true)}
          />
          {reducedMotion && !userStarted && (
            <button
              type="button"
              onClick={() => setUserStarted(true)}
              aria-label={`Play ${label} demo`}
              className="absolute inset-0 flex items-center justify-center bg-background/40 transition-colors hover:bg-background/20"
            >
              <span className="flex h-14 w-14 items-center justify-center border border-outline-variant bg-surface-container">
                <svg viewBox="0 0 16 16" width="18" height="18" aria-hidden="true">
                  <polygon points="5,3 13,8 5,13" fill="currentColor" className="text-on-surface" />
                </svg>
              </span>
            </button>
          )}
          <button
            type="button"
            onClick={() => setExpanded(true)}
            aria-label={`Expand ${label} demo`}
            className="absolute bottom-0 right-0 flex h-9 w-9 items-center justify-center border-l border-t border-outline-variant bg-background/80 text-on-surface-variant opacity-80 transition-all hover:bg-surface-container hover:text-on-surface group-hover:opacity-100"
          >
            <ExpandGlyph />
          </button>
          {expanded && <Lightbox src={src} label={label} onClose={close} />}
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
