import { useRef, type ReactNode } from 'react';
import { useInView } from '../hooks';

interface RevealProps {
  children: ReactNode;
  /** Stagger delay in ms, applied to the CSS transition. */
  delay?: number;
  className?: string;
}

/** Scroll-reveal wrapper: fades/slides children in the first time they enter
 *  the viewport. Static under prefers-reduced-motion (see landing.css). */
export default function Reveal({ children, delay = 0, className = '' }: RevealProps) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref);

  return (
    <div
      ref={ref}
      className={`reveal ${inView ? 'reveal-in' : ''} ${className}`}
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
    >
      {children}
    </div>
  );
}
