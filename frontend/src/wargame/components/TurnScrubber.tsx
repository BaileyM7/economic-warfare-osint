
export interface TurnScrubberProps {
  currentTurn: number;
  totalTurns: number;
  completedTurns: number;
  onChange: (turn: number) => void;
}

export function TurnScrubber({
  currentTurn,
  totalTurns,
  completedTurns,
  onChange,
}: TurnScrubberProps) {
  if (completedTurns === 0) return null;

  return (
    <div className="space-y-1.5">
      <div className="flex justify-between items-center">
        <span className="font-mono text-[10px] text-on-surface-variant uppercase tracking-widest">
          Turn Scrubber
        </span>
        <span className="font-mono text-[10px] text-cyber">
          {currentTurn} / {totalTurns}
        </span>
      </div>

      {/* Tick marks */}
      <div className="relative h-6">
        <div className="absolute inset-y-2.5 left-0 right-0 bg-surface-container-high" />
        <div
          className="absolute inset-y-2.5 left-0 bg-cyber/60"
          style={{ width: `${(currentTurn / totalTurns) * 100}%` }}
        />
        {/* Tick marks per turn */}
        <div className="absolute inset-y-0 left-0 right-0 flex items-center">
          {Array.from({ length: totalTurns }).map((_, i) => {
            const turn = i + 1;
            const pos = (turn / totalTurns) * 100;
            const isDone = turn <= completedTurns;
            return (
              <button
                key={turn}
                aria-label={`Go to turn ${turn}`}
                onClick={() => onChange(turn)}
                disabled={!isDone}
                className="absolute top-0 bottom-0 w-1 flex items-center justify-center group"
                style={{ left: `${pos}%`, transform: 'translateX(-50%)' }}
              >
                <span
                  className={[
                    'w-0.5 h-3 transition-colors',
                    isDone ? 'bg-cyber/60 group-hover:bg-cyber' : 'bg-outline-variant/40',
                  ].join(' ')}
                />
              </button>
            );
          })}
        </div>

        {/* Draggable range input overlay */}
        <input
          type="range"
          min={0}
          max={completedTurns}
          value={currentTurn}
          onChange={(e) => onChange(Number(e.target.value))}
          className="absolute inset-0 w-full opacity-0 cursor-pointer"
          aria-label="Turn scrubber"
          aria-valuenow={currentTurn}
          aria-valuemin={0}
          aria-valuemax={completedTurns}
        />
      </div>
    </div>
  );
}
