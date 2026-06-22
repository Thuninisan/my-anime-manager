import type { SubscriptionOut } from '@/types/preview';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import SubscriptionCard from './SubscriptionCard';

interface Props {
  subscriptions: SubscriptionOut[];
  loading: boolean;
  onOpenHistory: (bangumiId: number) => void;
  onUnsubscribe: (bangumiId: number) => void;
  onActivate: (bangumiId: number) => Promise<void>;
}

export default function SubscriptionList({
  subscriptions, loading, onOpenHistory, onUnsubscribe, onActivate,
}: Props) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>我的订阅 ({subscriptions.length})</CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <p className="text-center py-6 text-muted-foreground">加载中...</p>
        ) : subscriptions.length === 0 ? (
          <p className="text-center py-6 text-muted-foreground text-sm">暂无订阅</p>
        ) : (
          <div className="flex flex-col gap-2">
            {subscriptions.map(s => (
              <SubscriptionCard
                key={s.bangumi_id}
                subscription={s}
                onOpenHistory={onOpenHistory}
                onUnsubscribe={onUnsubscribe}
                onActivate={onActivate}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
