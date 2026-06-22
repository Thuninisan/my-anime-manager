import type { SubscriptionOut } from '@/types/preview';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { renderTags } from './FeedPreview';

interface Props {
  subscription: SubscriptionOut;
  onOpenHistory: (bangumiId: number) => void;
  onUnsubscribe: (bangumiId: number) => void;
  onActivate: (bangumiId: number) => Promise<void>;
}

export default function SubscriptionCard({ subscription: s, onOpenHistory, onUnsubscribe, onActivate }: Props) {
  const totalEps = s.bgm_sortrange ? s.bgm_sortrange[1] - s.bgm_sortrange[0] + 1 : 0;
  const hue = (s.bangumi_id * 137) % 360;

  return (
    <Card size="sm">
      <CardContent className="flex items-center gap-4 p-3">
        {/* Cover placeholder */}
        <div
          className="shrink-0 w-[92px] h-[130px] rounded-md flex items-center justify-center cursor-pointer text-xs text-white/60"
          style={{ background: `linear-gradient(135deg, hsl(${hue},50%,40%), hsl(${(hue+40)%360},40%,25%))` }}
          onClick={() => onOpenHistory(s.bangumi_id)}
        >
          <span className="text-2xl font-bold opacity-30">{(s.name || '?')[0]}</span>
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0 self-stretch flex flex-col justify-between">
          <div>
            <p
              className="text-sm font-semibold truncate cursor-pointer hover:text-primary transition-colors"
              onClick={() => onOpenHistory(s.bangumi_id)}
            >
              {s.name}
            </p>
            <div className="flex flex-wrap items-center gap-1.5 mt-1">
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                BGM {s.bangumi_id}
              </span>
              {s.bgm_season ? (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                  S{s.bgm_season}
                </span>
              ) : null}
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                s.active !== 0
                  ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                  : 'bg-muted text-muted-foreground'
              }`}>
                {s.active !== 0 ? '启用' : '已完成'}
              </span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground truncate max-w-[120px]" title={s.subgroup_name}>
                {s.subgroup_name || '未知字幕组'}
              </span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-600 dark:text-amber-400">
                0 / {totalEps || '*'}
              </span>
              {s.backup_subgroup_name && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground" title={s.backup_subgroup_name}>
                  备用RSS
                </span>
              )}
            </div>
            <div className="flex flex-wrap gap-1 mt-1.5">
              {s.filter_tags.length > 0
                ? renderTags(s.filter_tags)
                : <span className="text-[10px] text-muted-foreground">不限</span>
              }
              {s.backup_filter_tags.length > 0 && s.backup_subgroup_name && (
                <>{renderTags(s.backup_filter_tags.map(t => `备:${t}`))}</>
              )}
            </div>
          </div>
          {s.updated_at && (
            <p className="text-[10px] text-muted-foreground mt-1">
              更新于 {s.updated_at.slice(0, 16).replace('T', ' ')}
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="shrink-0 flex flex-col gap-1.5 self-stretch justify-center">
          <Button variant="outline" size="sm" className="text-xs h-7 w-16"
            onClick={() => onOpenHistory(s.bangumi_id)}>历史</Button>
          {s.active === 0 ? (
            <Button variant="outline" size="sm" className="text-xs h-7 w-16"
              onClick={() => onActivate(s.bangumi_id)}>恢复</Button>
          ) : (
            <Button variant="outline" size="sm" className="text-xs h-7 w-16 text-destructive border-destructive/30 hover:bg-destructive/10"
              onClick={() => onUnsubscribe(s.bangumi_id)}>取消</Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
