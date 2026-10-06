import { useEffect, useState } from 'react';
import { getCalendar, type CalendarDay } from '@/api/exploreApi';
import { useNavigate } from 'react-router-dom';
import PosterCard from '@/components/shared/PosterCard';

const weekdays = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日'];

export default function ExplorePage() {
  const navigate = useNavigate();
  const [selectedDay, setSelectedDay] = useState(() => (new Date().getDay() + 6) % 7 + 1);
  const [calendar, setCalendar] = useState<CalendarDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    getCalendar()
      .then((days) => { if (active) setCalendar(days); })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : '无法加载每日放送'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const items = calendar.find((day) => day.weekday === selectedDay)?.items ?? [];

  return (
    <section className="space-y-5">
      <div className="rounded-xl bg-card p-5 shadow-sm">
        <h1 className="text-lg font-semibold">每日放送</h1>
        <p className="text-sm text-muted-foreground">来自 Bangumi 的每周动画放送表</p>
      </div>
      <div className="flex gap-2 overflow-x-auto pb-1" aria-label="选择星期几">
        {weekdays.map((label, index) => {
          const day = index + 1;
          return (
            <button key={day} type="button" onClick={() => setSelectedDay(day)}
              aria-pressed={selectedDay === day}
              className={`shrink-0 rounded-lg px-4 py-2 text-sm font-medium transition-colors cursor-pointer ${selectedDay === day ? 'bg-primary text-primary-foreground' : 'bg-card text-muted-foreground hover:bg-muted'}`}>
              {label}
            </button>
          );
        })}
      </div>
      {loading && <p className="text-sm text-muted-foreground">加载中…</p>}
      {error && <p role="alert" className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
      {!loading && !error && <>
        <h2 className="text-base font-semibold">{weekdays[selectedDay - 1]} · {items.length} 部</h2>
        {items.length === 0 && <p className="rounded-xl bg-card p-8 text-center text-sm text-muted-foreground">当天暂无放送番剧</p>}
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {items.map((item) => (
            <PosterCard key={item.id} bangumiId={item.id} name={item.name}
              posterUrl={item.poster_url} rating={item.rating}
              subtitle={item.original_name !== item.name ? item.original_name : item.air_date}
              onClick={() => navigate(`/rss?${new URLSearchParams({ bangumi_id: String(item.id), name: item.name, name_original: item.original_name })}`)} />
          ))}
        </div>
      </>}
    </section>
  );
}
