/**
 * Loader — pulse-themed globe overlay.
 * Three concentric rings animate outward in cyan (cyber color).
 */

export interface LoaderProps {
  message?: string;
}

export function Loader({ message = 'Loading…' }: LoaderProps) {
  return (
    <div
      className="absolute inset-0 flex flex-col items-center justify-center gap-6 z-30"
      role="status"
      aria-label={message}
    >
      {/* Three concentric pulse rings */}
      <div className="relative w-20 h-20">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="absolute inset-0 border border-cyber"
            style={{
              animation: `pulse-ring 1.8s cubic-bezier(0.215, 0.61, 0.355, 1) ${
                i * 0.5
              }s infinite`,
              opacity: 0,
            }}
          />
        ))}
        {/* Center dot */}
        <span className="absolute inset-4 border border-cyber/40 flex items-center justify-center">
          <span className="w-3 h-3 bg-cyber animate-pulse-glow" />
        </span>
      </div>

      <p className="font-mono text-[10px] text-cyber/70 uppercase tracking-widest">
        {message}
      </p>
    </div>
  );
}
