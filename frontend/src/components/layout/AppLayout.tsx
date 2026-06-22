import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Toaster } from 'sonner';
import { useTheme } from '@/hooks/useTheme';
import {
  IconSpa, IconDashboard, IconMemory, IconRss, IconSettings,
  IconAddCircle, IconSearch, IconNotifications, IconSun, IconMoon, IconUser,
} from '@/components/icons';

/* Page metadata for header display */
const PAGE_META: Record<string, { title: string; Icon: typeof IconMemory }> = {
  '/torrent': { title: 'Torrent Processing', Icon: IconMemory },
  '/rss': { title: 'RSS Management', Icon: IconRss },
  '/settings': { title: 'Settings', Icon: IconSettings },
};

export default function AppLayout() {
  const { theme, toggleTheme } = useTheme();
  const location = useLocation();
  const navigate = useNavigate();

  const currentPage = PAGE_META[location.pathname] || PAGE_META['/torrent'];
  const PageIcon = currentPage.Icon;

  return (
    <div className="flex min-h-screen bg-background custom-scrollbar">
      <Toaster richColors closeButton />

      {/* ═══ Fixed Sidebar ═══ */}
      <aside className="h-screen w-64 fixed left-0 top-0 bg-card shadow-md flex flex-col gap-2 p-6 z-30">
        {/* Logo */}
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-full bg-accent flex items-center justify-center text-white sakura-shadow">
            <IconSpa />
          </div>
          <div>
            <h1 className="text-lg font-bold text-primary leading-tight">Anime Manager</h1>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/60">
              Sakura Breeze
            </p>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 flex flex-col gap-1">
          <button className="flex items-center gap-3 p-3 text-muted-foreground hover:bg-muted transition-all rounded-lg group cursor-pointer text-left">
            <span className="group-hover:scale-110 transition-transform"><IconDashboard /></span>
            <span className="text-xs font-semibold tracking-wide">Dashboard</span>
          </button>

          <button
            onClick={() => navigate('/torrent')}
            className={`flex items-center gap-3 p-3 rounded-lg group cursor-pointer text-left transition-all ${
              location.pathname === '/torrent'
                ? 'text-primary font-bold bg-accent/20 border-l-2 border-primary'
                : 'text-muted-foreground hover:bg-muted border-l-2 border-transparent'
            }`}
          >
            <span className="group-hover:scale-110 transition-transform"><IconMemory /></span>
            <span className="text-xs font-semibold tracking-wide">Torrent Processing</span>
          </button>

          <button
            onClick={() => navigate('/rss')}
            className={`flex items-center gap-3 p-3 rounded-lg group cursor-pointer text-left transition-all ${
              location.pathname === '/rss'
                ? 'text-primary font-bold bg-accent/20 border-l-2 border-primary'
                : 'text-muted-foreground hover:bg-muted border-l-2 border-transparent'
            }`}
          >
            <span className="group-hover:scale-110 transition-transform"><IconRss /></span>
            <span className="text-xs font-semibold tracking-wide">RSS Management</span>
          </button>

          <button
            onClick={() => navigate('/settings')}
            className={`flex items-center gap-3 p-3 rounded-lg group cursor-pointer text-left transition-all ${
              location.pathname === '/settings'
                ? 'text-primary font-bold bg-accent/20 border-l-2 border-primary'
                : 'text-muted-foreground hover:bg-muted border-l-2 border-transparent'
            }`}
          >
            <span className="group-hover:scale-110 transition-transform"><IconSettings /></span>
            <span className="text-xs font-semibold tracking-wide">Settings</span>
          </button>
        </nav>

        {/* Bottom CTA */}
        <button
          onClick={() => navigate('/rss')}
          className="mt-auto bg-primary text-primary-foreground py-3 px-6 rounded-lg text-xs font-semibold tracking-wide flex items-center justify-center gap-2 shadow-md hover:opacity-90 active:scale-95 transition-all cursor-pointer"
        >
          <IconAddCircle />
          Add New RSS
        </button>

        {/* Theme toggle */}
        <button
          onClick={toggleTheme}
          className="flex items-center gap-3 p-3 text-muted-foreground hover:bg-muted transition-all rounded-lg cursor-pointer text-left"
        >
          <span>{theme === 'dark' ? <IconSun /> : <IconMoon />}</span>
          <span className="text-xs font-semibold tracking-wide">
            {theme === 'dark' ? 'Light Mode' : 'Dark Mode'}
          </span>
        </button>
      </aside>

      {/* ═══ Main Content ═══ */}
      <main className="flex-1 ml-64 p-6 pb-24">
        {/* ── Sticky Top Header ── */}
        <header className="sticky top-0 z-20 bg-background/80 backdrop-blur-md shadow-sm flex justify-between items-center px-6 py-3 max-w-[1200px] mx-auto mb-6 rounded-xl">
          <div className="flex items-center gap-3">
            <span className="text-primary"><PageIcon /></span>
            <h2 className="text-lg font-bold text-foreground">{currentPage.title}</h2>
          </div>
          <div className="flex items-center gap-6">
            <div className="relative hidden md:block">
              <input
                className="bg-muted/50 border-0 rounded-full px-6 py-1.5 text-sm w-64 focus:outline-none focus:ring-2 focus:ring-accent/50 placeholder:text-muted-foreground/50"
                placeholder="Search tasks..."
                type="text"
              />
              <span className="absolute right-3 top-1.5 text-muted-foreground/50"><IconSearch /></span>
            </div>
            <div className="flex items-center gap-3">
              <button className="p-3 hover:bg-muted rounded-full transition-colors relative cursor-pointer">
                <span className="text-muted-foreground"><IconNotifications /></span>
                <span className="absolute top-2 right-2 w-2 h-2 bg-primary rounded-full border-2 border-background" />
              </button>
              <button
                onClick={toggleTheme}
                className="flex items-center gap-1 p-1 pr-3 hover:bg-muted rounded-full transition-colors border border-border/30 cursor-pointer"
              >
                <div className="w-8 h-8 rounded-full bg-accent/20 border-2 border-accent shadow-sm flex items-center justify-center">
                  <span className="text-sm text-accent font-bold">
                    {theme === 'dark' ? '🌙' : '☀️'}
                  </span>
                </div>
                <span className="text-muted-foreground ml-1"><IconUser /></span>
              </button>
            </div>
          </div>
        </header>

        {/* ── Page Content ── */}
        <div className="max-w-[1200px] mx-auto space-y-6">
          <Outlet />
        </div>
      </main>

    </div>
  );
}
