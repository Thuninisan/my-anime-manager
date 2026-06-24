const AVAILABLE_TAGS = ['简体', '繁体', '日语', '内封', '内嵌', '双语', '1080p', '720p'];

export const TAG_COLORS: Record<string, string> = {
  '简体': 'bg-sky-500/15 text-sky-600 dark:text-sky-400',
  '繁体': 'bg-purple-500/15 text-purple-600 dark:text-purple-400',
  '日语': 'bg-pink-500/15 text-pink-600 dark:text-pink-400',
  '内封': 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  '内嵌': 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
  '双语': 'bg-cyan-500/15 text-cyan-600 dark:text-cyan-400',
  '1080p': 'bg-blue-500/15 text-blue-600 dark:text-blue-400',
  '720p': 'bg-orange-500/15 text-orange-600 dark:text-orange-400',
};

interface Props {
  selectedTags: string[];
  onToggleTag: (tag: string) => void;
}

export default function TagFilterPanel({ selectedTags, onToggleTag }: Props) {
  return (
    <div className="bg-muted/40 rounded-lg p-3 border border-border/50">
      <div className="text-xs text-muted-foreground mb-2">选择字幕过滤标签（需同时满足）:</div>
      <div className="flex flex-wrap gap-1.5">
        {AVAILABLE_TAGS.map(tag => {
          const active = selectedTags.includes(tag);
          return (
            <button
              key={tag}
              className={`text-xs px-2 py-1 rounded border cursor-pointer transition ${
                active
                  ? TAG_COLORS[tag] + ' border-current'
                  : 'border-border text-muted-foreground hover:text-foreground bg-background'
              }`}
              onClick={() => onToggleTag(tag)}
            >
              {active ? '✓ ' : ''}{tag}
            </button>
          );
        })}
      </div>
    </div>
  );
}
