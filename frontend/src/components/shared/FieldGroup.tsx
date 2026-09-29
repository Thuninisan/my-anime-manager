import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

interface Props {
  id: string;
  label: string;
  hint?: string;
  type?: 'text' | 'number' | 'password';
  value: string;
  placeholder?: string;
  dirty?: boolean;
  onChange: (value: string) => void;
}

export default function FieldGroup({ id, label, hint, type = 'text', value, placeholder, dirty, onChange }: Props) {
  return (
    <div className={cn(dirty && 'ring-1 ring-yellow-500/30 rounded-lg p-2 -mx-2')}>
      <label htmlFor={id} className="text-sm font-medium flex items-center gap-1.5">
        {label}
        {dirty && <span className="w-2 h-2 rounded-full bg-yellow-400" />}
      </label>
      <Input
        id={id}
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={e => onChange(e.target.value)}
        className="mt-1"
      />
      {hint && <p className="text-xs text-muted-foreground mt-1">{hint}</p>}
    </div>
  );
}
