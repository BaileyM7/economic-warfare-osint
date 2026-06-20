/**
 * FilmGrainOverlay — fixed-position decorative overlay per DESIGN.md.
 * 3% opacity SVG noise texture; pointer-events: none.
 * Rendered in app/layout.tsx so it's always on top.
 * This component is kept for explicit usage within panels if needed.
 */

export function FilmGrainOverlay() {
  return (
    <div
      aria-hidden="true"
      className="fixed inset-0 z-[200] pointer-events-none"
      style={{
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
        opacity: 0.03,
      }}
    />
  );
}
