import { cn } from '@/lib/utils';

interface Props {
  value: number;
  min: number;
  max: number;
  step: number;
  onValueChange: (v: number) => void;
  onValueCommit?: (v: number) => void;
}

export function Slider({ value, min, max, step, onValueChange, onValueCommit }: Props) {
  return (
    <input
      type="range"
      min={min}
      max={max}
      step={step}
      value={value}
      onChange={e => onValueChange(parseInt(e.target.value, 10))}
      onMouseUp={e => onValueCommit?.(parseInt((e.target as HTMLInputElement).value, 10))}
      onTouchEnd={e => onValueCommit?.(parseInt((e.target as HTMLInputElement).value, 10))}
      className={cn(
        "w-full h-1.5 rounded-full appearance-none bg-muted cursor-pointer accent-primary",
        "[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:size-4",
        "[&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-primary",
        "[&::-webkit-slider-thumb]:shadow-sm [&::-webkit-slider-thumb]:cursor-pointer",
      )}
    />
  );
}
