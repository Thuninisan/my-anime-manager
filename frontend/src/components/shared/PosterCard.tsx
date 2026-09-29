import { useState } from 'react';

interface PosterCardProps {
  bangumiId?: number;
  name: string;
  posterUrl?: string;
  rating?: number;
  tags?: string[];
  subtitle?: string;
  onClick?: () => void;
}

export default function PosterCard({ bangumiId, name, posterUrl, rating, tags = [], subtitle, onClick }: PosterCardProps) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const hue = ((bangumiId || 0) * 137) % 360;
  const content = (
    <>
      <div className="aspect-[2/3] relative overflow-hidden"
        style={{ background: `linear-gradient(135deg, hsl(${hue},45%,35%), hsl(${(hue + 40) % 360},35%,20%))` }}>
        {posterUrl && failedUrl !== posterUrl ? (
          <img src={posterUrl} alt={name} className="absolute inset-0 w-full h-full object-cover" loading="lazy" onError={() => setFailedUrl(posterUrl)} />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center p-4">
            <span className="text-3xl font-bold text-white/25">{name[0] || '?'}</span>
          </div>
        )}
        {rating != null && rating > 0 && (
          <div className="absolute top-3 left-3 bg-secondary text-white text-[10px] font-bold px-2 py-1 rounded-full glass-effect">
            BGM {Number(rating).toFixed(1)} / 10
          </div>
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent opacity-60 pointer-events-none" />
      </div>
      <div className="p-4 space-y-3">
        <h3 className="text-sm font-semibold truncate leading-tight" title={name}>{name}</h3>
        {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
        <div className="flex flex-wrap gap-1 min-h-5">
          {tags.map((tag) => <span key={tag} className="text-[9px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{tag}</span>)}
        </div>
      </div>
    </>
  );

  const classes = 'bg-card rounded-xl overflow-hidden sakura-shadow w-full text-left';
  return onClick ? (
    <button type="button" onClick={onClick} className={`${classes} cursor-pointer transition-all hover:-translate-y-1 hover:shadow-lg focus-visible:outline-2 focus-visible:outline-primary`}>
      {content}
    </button>
  ) : <article className={classes}>{content}</article>;
}
