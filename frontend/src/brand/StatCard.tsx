/**
 * Agile Defense stat card — a navy block: big value, a thin colored bar, then a
 * colored label. This is the ONLY place the bounded secondary accent palette
 * (blue / light-blue / orange / red) is allowed to appear. Sharp corners, no
 * shadow.
 */
type StatColor = 'blue' | 'light' | 'orange' | 'red';

interface StatCardProps {
  value: string;
  label: string;
  color?: StatColor;
  className?: string;
}

const COLORS: Record<StatColor, string> = {
  blue: 'text-primary',
  light: 'text-secondary',
  orange: 'text-tertiary',
  red: 'text-accent-bright',
};

const BARS: Record<StatColor, string> = {
  blue: 'bg-primary',
  light: 'bg-secondary',
  orange: 'bg-tertiary',
  red: 'bg-accent',
};

export default function StatCard({ value, label, color = 'blue', className = '' }: StatCardProps) {
  return (
    <div
      className={`bg-surface-container-low flex flex-col justify-center gap-3 p-6 ${className}`}
    >
      <span className={`font-headline text-4xl font-medium leading-none ${COLORS[color]}`}>
        {value}
      </span>
      <span className={`h-2 w-full ${BARS[color]}`} />
      <span className={`font-label text-base font-medium leading-tight ${COLORS[color]}`}>
        {label}
      </span>
    </div>
  );
}
