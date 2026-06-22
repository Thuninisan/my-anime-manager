import type { SubscriptionOut } from '@/types/preview';

interface Props {
  subscription: SubscriptionOut;
  onOpenHistory: (bangumiId: number) => void;
  onUnsubscribe: (bangumiId: number) => void;
  onActivate: (bangumiId: number) => Promise<void>;
}

export default function SubscriptionCard({ subscription: s, onOpenHistory, onUnsubscribe, onActivate }: Props) {
  const totalEps = s.bgm_sortrange ? s.bgm_sortrange[1] - s.bgm_sortrange[0] + 1 : 0;
  const downloaded = 0; // placeholder until we have real data in list view
  const progressPct = totalEps > 0 ? (downloaded / totalEps) * 100 : 0;
  const isActive = s.active !== 0;
  const hue = (s.bangumi_id * 137) % 360;

  return (
    <div className="group relative bg-card rounded-xl overflow-hidden sakura-shadow transition-all duration-300 hover:-translate-y-1 hover:shadow-lg">
      {/* Poster area */}
      <div
        className="aspect-[2/3] relative overflow-hidden cursor-pointer"
        style={{ background: `linear-gradient(135deg, hsl(${hue},45%,35%), hsl(${(hue+40)%360},35%,20%))` }}
        onClick={() => onOpenHistory(s.bangumi_id)}
      >
        {/* Title overlay on poster */}
        <div className="absolute inset-0 flex items-center justify-center p-4">
          <span className="text-3xl font-bold text-white/25">{(s.name || '?')[0]}</span>
        </div>

        {/* Rating badge */}
        <div className="absolute top-3 left-3 bg-secondary text-white text-[10px] font-bold px-2 py-1 rounded-full glass-effect">
          BGM {s.bangumi_id}
        </div>

        {/* Gradient overlay */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent opacity-60" />

        {/* Hover action overlay */}
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 opacity-0 group-hover:opacity-100 transition-all duration-300 translate-y-4 group-hover:translate-y-0 bg-primary/20 backdrop-blur-[2px]">
          <button
            className="bg-card text-primary p-3 rounded-full shadow-md hover:bg-accent hover:text-accent-foreground transition-colors cursor-pointer"
            onClick={(e) => { e.stopPropagation(); onOpenHistory(s.bangumi_id); }}
            title="Download History"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/>
            </svg>
          </button>
          {isActive ? (
            <button
              className="bg-card text-destructive p-3 rounded-full shadow-md hover:bg-destructive hover:text-destructive-foreground transition-colors cursor-pointer"
              onClick={(e) => { e.stopPropagation(); onUnsubscribe(s.bangumi_id); }}
              title="Unsubscribe"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          ) : (
            <button
              className="bg-card text-success p-3 rounded-full shadow-md hover:bg-success hover:text-white transition-colors cursor-pointer"
              onClick={(e) => { e.stopPropagation(); onActivate(s.bangumi_id); }}
              title="Restore"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* Info area */}
      <div className="p-4 space-y-3">
        <div className="flex justify-between items-start gap-2">
          <h3 className="text-sm font-semibold truncate leading-tight" title={s.name}>
            {s.name}
          </h3>
          <span className={`shrink-0 text-[10px] font-semibold px-2 py-1 rounded-full ${
            isActive
              ? 'bg-accent/15 text-accent'
              : 'bg-muted text-muted-foreground'
          }`}>
            {isActive ? 'Ongoing' : 'Completed'}
          </span>
        </div>
        <div className="space-y-1.5">
          <div className="flex justify-between text-[10px] font-semibold text-muted-foreground">
            <span>Progress</span>
            <span className="text-primary font-bold">{downloaded} / {totalEps || '?'}</span>
          </div>
          <div className="h-1.5 w-full bg-muted rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                isActive ? 'bg-accent' : 'bg-secondary'
              }`}
              style={{ width: `${isActive ? progressPct : 100}%` }}
            />
          </div>
        </div>
        {/* Tags */}
        <div className="flex flex-wrap gap-1">
          {s.subgroup_name && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground truncate max-w-[80px]" title={s.subgroup_name}>
              {s.subgroup_name}
            </span>
          )}
          {s.filter_tags.length > 0
            ? s.filter_tags.slice(0, 2).map(t => (
                <span key={t} className="text-[9px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{t}</span>
              ))
            : <span className="text-[9px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">Any</span>
          }
          {s.backup_subgroup_name && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-600 dark:text-amber-400" title={s.backup_subgroup_name}>
              backup
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
