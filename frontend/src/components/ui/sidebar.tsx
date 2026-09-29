import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useMemo,
} from "react";
import type { ReactNode, ComponentProps } from "react";
import { cn } from "@/lib/utils";

/* ======== Types ======== */
type SidebarState = "expanded" | "collapsed";

interface SidebarContextValue {
  state: SidebarState;
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
  isMobile: boolean;
}

/* ======== Context ======== */
const SidebarContext = createContext<SidebarContextValue | null>(null);

function useSidebar() {
  const ctx = useContext(SidebarContext);
  if (!ctx) throw new Error("useSidebar must be used within SidebarProvider");
  return ctx;
}

/* ======== Constants ======== */
const WIDTH_EXPANDED = "16rem";
const WIDTH_COLLAPSED = "3rem";
const STORAGE_KEY = "sidebar:state";

/* ======== Provider ======== */
interface SidebarProviderProps {
  children: ReactNode;
  defaultOpen?: boolean;
}

function SidebarProvider({
  children,
  defaultOpen = true,
}: SidebarProviderProps) {
  const [open, setOpenState] = useState(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored !== null ? stored === "true" : defaultOpen;
    } catch {
      return defaultOpen;
    }
  });

  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== "undefined" ? window.innerWidth < 768 : false,
  );

  // Listen for window resize
  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Keyboard shortcut: Ctrl+B
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "b" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        setOpenState((prev) => !prev);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Persist to localStorage
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(open));
    } catch {
      // localStorage unavailable
    }
  }, [open]);

  const setOpen = useCallback((v: boolean) => setOpenState(v), []);
  const toggle = useCallback(() => setOpenState((p) => !p), []);

  const value = useMemo<SidebarContextValue>(
    () => ({
      state: open ? "expanded" : "collapsed",
      open,
      setOpen,
      toggle,
      isMobile,
    }),
    [open, setOpen, toggle, isMobile],
  );

  return (
    <SidebarContext.Provider value={value}>
      <SidebarLayout>{children}</SidebarLayout>
    </SidebarContext.Provider>
  );
}

/* ======== Layout wrapper ======== */
function SidebarLayout({ children }: { children: ReactNode }) {
  const { isMobile } = useSidebar();
  return (
    <>
      <style>{`
        :root {
          --sidebar-width: ${WIDTH_EXPANDED};
          --sidebar-width-collapsed: ${WIDTH_COLLAPSED};
        }
        @media (max-width: 767px) {
          :root {
            --sidebar-width: 0px;
          }
        }
        /* Auto-adjust inset margin when sidebar is collapsed */
        [data-sidebar-layout] {
          --sidebar-width-current: var(--sidebar-width);
        }
        [data-sidebar-layout]:has([data-sidebar][data-state="collapsed"]) {
          --sidebar-width-current: var(--sidebar-width-collapsed);
        }
        @media (max-width: 767px) {
          [data-sidebar-layout] {
            --sidebar-width-current: 0px;
          }
        }
      `}</style>
      <div className="flex min-h-screen w-full" data-sidebar-layout="">
        {children}
        {/* Backdrop for mobile overlay */}
        {isMobile && <SidebarMobileBackdrop />}
      </div>
    </>
  );
}

function SidebarMobileBackdrop() {
  const { open, setOpen, isMobile } = useSidebar();
  if (!open || !isMobile) return null;
  return (
    <div
      className="fixed inset-0 z-30 bg-black/60 transition-opacity md:hidden"
      onClick={() => setOpen(false)}
    />
  );
}

/* ======== Sidebar Root ======== */
interface SidebarProps extends ComponentProps<"aside"> {
  children: ReactNode;
}

function Sidebar({ children, className, ...props }: SidebarProps) {
  const { open, isMobile } = useSidebar();

  return (
    <aside
      data-sidebar=""
      data-state={open ? "expanded" : "collapsed"}
      data-mobile={isMobile ? "" : undefined}
      className={cn(
        "fixed top-0 left-0 z-40 flex h-screen flex-col border-r bg-sidebar-background text-sidebar-foreground transition-all duration-200",
        // Width
        isMobile
          ? "w-[var(--sidebar-width)] data-[state=collapsed]:-translate-x-full"
          : "w-[var(--sidebar-width)] data-[state=collapsed]:w-[var(--sidebar-width-collapsed)]",
        // Mobile: overlay
        isMobile && "data-[state=collapsed]:pointer-events-none",
        className,
      )}
      {...props}
    >
      {children}
    </aside>
  );
}

/* ======== Sidebar Trigger (hamburger) ======== */
function SidebarTrigger({ className, ...props }: ComponentProps<"button">) {
  const { toggle } = useSidebar();

  return (
    <button
      data-sidebar="trigger"
      className={cn(
        "inline-flex items-center justify-center rounded-md size-8 text-muted-foreground hover:text-foreground hover:bg-accent transition cursor-pointer",
        className,
      )}
      onClick={toggle}
      title="Toggle sidebar (Ctrl+B)"
      {...props}
    >
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <rect width="18" height="18" x="3" y="3" rx="2" />
        <path d="M9 3v18" />
      </svg>
    </button>
  );
}

/* ======== Sidebar Inset (main content wrapper) ======== */
function SidebarInset({
  children,
  className,
  ...props
}: ComponentProps<"div">) {
  return (
    <div
      data-sidebar="inset"
      className={cn(
        "flex min-h-screen flex-1 flex-col transition-all duration-200",
        "ml-[var(--sidebar-width-current)]",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

/* ======== Sidebar Header ======== */
function SidebarHeader({
  children,
  className,
  ...props
}: ComponentProps<"div">) {
  return (
    <div
      data-sidebar="header"
      className={cn("flex flex-col gap-2 px-3 py-4", className)}
      {...props}
    >
      {children}
    </div>
  );
}

/* ======== Sidebar Content ======== */
function SidebarContent({
  children,
  className,
  ...props
}: ComponentProps<"div">) {
  return (
    <div
      data-sidebar="content"
      className={cn(
        "flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-3",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

/* ======== Sidebar Footer ======== */
function SidebarFooter({
  children,
  className,
  ...props
}: ComponentProps<"div">) {
  return (
    <div
      data-sidebar="footer"
      className={cn("flex flex-col gap-1 border-t p-3", className)}
      {...props}
    >
      {children}
    </div>
  );
}

/* ======== Sidebar Group ======== */
function SidebarGroup({
  children,
  className,
  ...props
}: ComponentProps<"div">) {
  return (
    <div
      data-sidebar="group"
      className={cn("flex flex-col gap-1", className)}
      {...props}
    >
      {children}
    </div>
  );
}

function SidebarGroupLabel({
  children,
  className,
  ...props
}: ComponentProps<"div">) {
  const { open } = useSidebar();
  if (!open) return null;

  return (
    <div
      data-sidebar="group-label"
      className={cn(
        "px-2 py-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

/* ======== Sidebar Menu ======== */
function SidebarMenu({
  children,
  className,
  ...props
}: ComponentProps<"ul">) {
  return (
    <ul
      data-sidebar="menu"
      className={cn("flex flex-col gap-1", className)}
      {...props}
    >
      {children}
    </ul>
  );
}

function SidebarMenuItem({
  children,
  className,
  ...props
}: ComponentProps<"li">) {
  return (
    <li
      data-sidebar="menu-item"
      className={cn("list-none", className)}
      {...props}
    >
      {children}
    </li>
  );
}

interface SidebarMenuButtonProps extends ComponentProps<"button"> {
  isActive?: boolean;
  icon?: ReactNode;
}

function SidebarMenuButton({
  children,
  className,
  isActive,
  icon,
  ...props
}: SidebarMenuButtonProps) {
  const { open } = useSidebar();

  return (
    <button
      data-sidebar="menu-button"
      data-active={isActive ? "" : undefined}
      className={cn(
        "flex w-full items-center gap-2 rounded-md px-2 py-2 text-sm font-medium cursor-pointer transition-colors",
        "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
        isActive &&
          "bg-sidebar-accent text-sidebar-accent-foreground",
        !open && "justify-center px-2",
        className,
      )}
      title={!open ? String(children) : undefined}
      {...props}
    >
      {icon && (
        <span className="flex items-center justify-center size-5 shrink-0">
          {icon}
        </span>
      )}
      {open && <span className="truncate">{children}</span>}
    </button>
  );
}

/* ======== Sidebar Separator ======== */
function SidebarSeparator({
  className,
  ...props
}: ComponentProps<"div">) {
  return (
    <div
      data-sidebar="separator"
      className={cn("mx-2 my-1 h-px bg-sidebar-border", className)}
      {...props}
    />
  );
}

export {
  SidebarProvider,
  useSidebar,
  Sidebar,
  SidebarTrigger,
  SidebarInset,
  SidebarHeader,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
  SidebarSeparator,
};
