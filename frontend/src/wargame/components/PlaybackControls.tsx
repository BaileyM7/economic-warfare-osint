
import { Pause, Play, Square } from 'lucide-react';
import type { SimulationStatus } from '@/lib/types/scenario';
import { IconButton } from './ui/IconButton';

export interface PlaybackControlsProps {
  simStatus: SimulationStatus;
  onPause: () => void;
  onResume: () => void;
  onAbort: () => void;
}

export function PlaybackControls({
  simStatus,
  onPause,
  onResume,
  onAbort,
}: PlaybackControlsProps) {
  if (simStatus !== 'running' && simStatus !== 'paused') return null;

  return (
    <div className="flex items-center gap-2">
      {simStatus === 'running' ? (
        <IconButton
          icon={<Pause size={14} />}
          label="Pause simulation"
          onClick={onPause}
          className="text-cyber hover:bg-cyber/10"
        />
      ) : (
        <IconButton
          icon={<Play size={14} />}
          label="Resume simulation"
          onClick={onResume}
          className="text-cyber hover:bg-cyber/10"
        />
      )}
      <IconButton
        icon={<Square size={14} />}
        label="Abort simulation"
        onClick={onAbort}
        className="text-kinetic hover:bg-kinetic/10"
      />
      <span className="font-mono text-[10px] text-on-surface-variant uppercase tracking-widest ml-1">
        {simStatus === 'running' ? 'LIVE' : 'PAUSED'}
      </span>
    </div>
  );
}
