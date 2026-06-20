
import type { ButtonHTMLAttributes, ReactNode } from 'react';

export type IconButtonSize = 'sm' | 'md';

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon: ReactNode;
  label: string; // aria-label
  size?: IconButtonSize;
}

const sizeClasses: Record<IconButtonSize, string> = {
  sm: 'w-7 h-7',
  md: 'w-9 h-9',
};

export function IconButton({
  icon,
  label,
  size = 'md',
  className = '',
  ...props
}: IconButtonProps) {
  return (
    <button
      aria-label={label}
      className={[
        'inline-flex items-center justify-center',
        'text-on-surface-variant hover:text-on-surface transition-colors',
        'disabled:opacity-40 disabled:cursor-not-allowed',
        sizeClasses[size],
        className,
      ].join(' ')}
      {...props}
    >
      {icon}
    </button>
  );
}
