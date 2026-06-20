
import { Component, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * Class-based ErrorBoundary — React requires class components for error boundaries.
 * Wraps the Globe canvas to prevent WebGL errors from crashing the whole app.
 */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error) {
    console.error('[ErrorBoundary] caught:', error);
  }

  reset = () => this.setState({ hasError: false, error: null });

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="absolute inset-0 flex items-center justify-center bg-background/80 z-30">
          <div className="w-[420px] border border-kinetic/30 bg-surface-container-low p-6 space-y-4">
            <div className="flex items-center gap-3">
              <span className="bg-kinetic text-[10px] font-mono px-2 py-0.5 font-bold text-background">
                RENDER ERROR
              </span>
            </div>
            <p className="font-mono text-xs text-on-surface leading-relaxed">
              {this.state.error?.message ?? 'Globe renderer failed.'}
            </p>
            <button
              onClick={this.reset}
              className="w-full h-10 bg-cyber text-on-primary font-mono text-xs font-bold tracking-widest hover:brightness-110 transition-all"
            >
              RETRY
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
