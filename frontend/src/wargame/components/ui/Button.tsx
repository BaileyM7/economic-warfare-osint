
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Loader2 } from 'lucide-react';

export type ButtonVariant = 'primary' | 'ghost' | 'destructive';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  children: ReactNode;
}

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    'bg-cyber text-on-primary font-bold hover:brightness-110 active:scale-[0.98]',
  ghost:
    'border border-outline-variant text-on-surface-variant hover:text-on-surface hover:border-cyber/50 bg-transparent',
  destructive:
    'bg-kinetic/10 border border-kinetic/30 text-kinetic hover:bg-kinetic/20',
};

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-[10px]',
  md: 'h-10 px-4 text-xs',
  lg: 'h-12 px-6 text-xs',
};

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  disabled,
  className = '',
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      disabled={disabled || loading}
      className={[
        'inline-flex items-center justify-center gap-2',
        'font-mono tracking-widest uppercase',
        'transition-all duration-150',
        'disabled:opacity-40 disabled:cursor-not-allowed',
        variantClasses[variant],
        sizeClasses[size],
        className,
      ].join(' ')}
      {...props}
    >
      {loading && <Loader2 size={12} className="animate-spin" />}
      {children}
    </button>
  );
}
