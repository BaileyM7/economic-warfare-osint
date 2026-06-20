import type { ButtonHTMLAttributes, ReactNode } from 'react';

/**
 * Agile Defense action button. Sharp rectangle, no shadow. The primary variant
 * carries the single bounded red accent — centralizing it here keeps red off
 * every other interactive element (the brand rule: red is the one accent, used
 * sparingly for true CTAs / active states).
 *
 *  - primary    red accent CTA
 *  - secondary  flat navy block (surface-container)
 *  - ghost      transparent, hairline border
 */
type Variant = 'primary' | 'secondary' | 'ghost';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  icon?: ReactNode;
  iconRight?: boolean;
  children: ReactNode;
}

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent text-white hover:bg-accent-hover',
  secondary: 'bg-surface-container text-on-surface hover:bg-surface-container-high',
  ghost: 'bg-transparent text-on-surface border border-outline-variant hover:bg-surface-container',
};

export default function Button({
  variant = 'primary',
  icon,
  iconRight = true,
  children,
  className = '',
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 h-11 px-6 font-label text-sm font-medium uppercase tracking-wide transition-colors duration-150 disabled:opacity-40 disabled:cursor-not-allowed ${VARIANTS[variant]} ${className}`}
      {...rest}
    >
      {icon && !iconRight ? <span className="inline-flex shrink-0">{icon}</span> : null}
      <span>{children}</span>
      {icon && iconRight ? <span className="inline-flex shrink-0">{icon}</span> : null}
    </button>
  );
}
