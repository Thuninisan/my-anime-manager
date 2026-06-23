import { useMemo } from 'react';
import type { SubscriptionOut } from '@/types/preview';
import SubscriptionCard from './SubscriptionCard';

interface Props {
  subscriptions: SubscriptionOut[];
  loading: boolean;
  onOpenHistory: (bangumiId: number, subscription: SubscriptionOut) => void;
  onUnsubscribe: (bangumiId: number, subscription: SubscriptionOut) => void;
  onActivate: (bangumiId: number) => Promise<void>;
}

function SectionHeader({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <h3 className="text-lg font-semibold text-primary flex items-center gap-2">
      {icon}
      {title}
    </h3>
  );
}

function AddCard({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="group flex flex-col items-center justify-center gap-6 aspect-[2/3] border-4 border-dashed border-border rounded-xl hover:border-accent hover:bg-accent/5 transition-all duration-300 cursor-pointer"
    >
      <div className="w-16 h-16 rounded-full bg-muted flex items-center justify-center group-hover:bg-accent group-hover:text-accent-foreground transition-colors">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
        </svg>
      </div>
      <span className="text-sm font-semibold text-muted-foreground group-hover:text-primary transition-colors">
        Add New Subscription
      </span>
    </button>
  );
}

export default function SubscriptionList({
  subscriptions, loading, onOpenHistory, onUnsubscribe, onActivate,
}: Props) {
  const { ongoing, completed } = useMemo(() => {
    const active: SubscriptionOut[] = [];
    const done: SubscriptionOut[] = [];
    for (const s of subscriptions) {
      if (s.active !== 0) active.push(s);
      else done.push(s);
    }
    return { ongoing: active, completed: done };
  }, [subscriptions]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="w-8 h-8 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    );
  }

  if (subscriptions.length === 0) {
    return (
      <div className="text-center py-16">
        <p className="text-muted-foreground text-sm">No subscriptions yet. Search a Bangumi ID to get started.</p>
      </div>
    );
  }

  return (
    <div className="space-y-10">
      {/* Ongoing */}
      {ongoing.length > 0 && (
        <div className="space-y-5">
          <SectionHeader
            icon={
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="7" width="20" height="15" rx="2" ry="2"/><polyline points="17 2 12 7 7 2"/>
              </svg>
            }
            title="Ongoing"
          />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
            {ongoing.map(s => (
              <SubscriptionCard
                key={s.bangumi_id}
                subscription={s}
                onOpenHistory={onOpenHistory}
                onUnsubscribe={onUnsubscribe}
                onActivate={onActivate}
              />
            ))}
          </div>
        </div>
      )}

      {/* Completed */}
      {completed.length > 0 && (
        <div className="space-y-5">
          <SectionHeader
            icon={
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
              </svg>
            }
            title="Completed"
          />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
            {completed.map(s => (
              <SubscriptionCard
                key={s.bangumi_id}
                subscription={s}
                onOpenHistory={onOpenHistory}
                onUnsubscribe={onUnsubscribe}
                onActivate={onActivate}
              />
            ))}
            <AddCard onClick={() => {}} />
          </div>
        </div>
      )}
    </div>
  );
}
