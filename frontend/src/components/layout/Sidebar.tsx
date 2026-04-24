import { NavLink, useLocation } from "react-router-dom";
import { ChevronLeft, ChevronRight, Settings, HelpCircle, X } from "lucide-react";
import { useEffect } from "react";
import { useSettingsStore } from "@/stores/settingsStore";
import { cn } from "@/components/ui/cn";
import { Tooltip } from "@/components/ui/Tooltip";
import { MODULES } from "./modules";

function SidebarContent({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  const toggle = useSettingsStore((s) => s.toggleSidebar);

  return (
    <>
      <nav className="flex flex-1 flex-col gap-1 p-2">
        {MODULES.map((m) => {
          const Icon = m.icon;
          const link = (
            <NavLink
              key={m.key}
              to={m.path}
              onClick={onNavigate}
              className={({ isActive }) =>
                cn(
                  "group flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                  "hover:bg-bgHover",
                  isActive ? "bg-bgHover text-textPrimary" : "text-textSecondary",
                  collapsed && "justify-center px-0",
                )
              }
              style={({ isActive }) =>
                isActive ? ({ borderLeft: `3px solid ${m.color}`, paddingLeft: collapsed ? undefined : 9 } as const) : undefined
              }
            >
              <Icon size={20} strokeWidth={1.5} style={{ color: m.color }} />
              {!collapsed && <span className="truncate">{m.label}</span>}
            </NavLink>
          );
          return collapsed ? (
            <Tooltip key={m.key} label={m.label} side="right">
              {link}
            </Tooltip>
          ) : (
            link
          );
        })}
      </nav>
      <div className="mt-auto flex flex-col gap-1 border-t border-borderSubtle p-2">
        {[
          { to: "/settings", icon: Settings, label: "Settings" },
          { to: "/help", icon: HelpCircle, label: "Help" },
        ].map((it) => {
          const Icon = it.icon;
          const link = (
            <NavLink
              to={it.to}
              onClick={onNavigate}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm text-textSecondary hover:bg-bgHover hover:text-textPrimary",
                  isActive && "bg-bgHover text-textPrimary",
                  collapsed && "justify-center px-0",
                )
              }
            >
              <Icon size={20} strokeWidth={1.5} />
              {!collapsed && <span>{it.label}</span>}
            </NavLink>
          );
          return collapsed ? (
            <Tooltip key={it.to} label={it.label} side="right">
              {link}
            </Tooltip>
          ) : (
            <div key={it.to}>{link}</div>
          );
        })}
        <button
          onClick={toggle}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="mt-1 hidden items-center justify-center rounded-md py-2 text-textMuted hover:bg-bgHover hover:text-textPrimary md:flex"
        >
          {collapsed ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
        </button>
      </div>
    </>
  );
}

function MobileSidebar() {
  const open = useSettingsStore((s) => s.mobileSidebarOpen);
  const setOpen = useSettingsStore((s) => s.setMobileSidebarOpen);
  const location = useLocation();

  useEffect(() => {
    setOpen(false);
  }, [location.pathname, setOpen]);

  if (!open) return null;

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/50 md:hidden" onClick={() => setOpen(false)} />
      <aside className="fixed inset-y-0 left-0 z-50 flex w-64 flex-col bg-bgSecondary shadow-xl md:hidden">
        <div className="flex items-center justify-between border-b border-borderSubtle px-4 py-3">
          <div className="flex items-center gap-2">
            <div className="h-6 w-6 rounded-sm bg-primary-500" />
            <span className="text-sm font-bold tracking-wide">TradeCore</span>
          </div>
          <button
            onClick={() => setOpen(false)}
            className="rounded-md p-1.5 text-textSecondary hover:bg-bgHover hover:text-textPrimary"
          >
            <X size={20} />
          </button>
        </div>
        <SidebarContent collapsed={false} onNavigate={() => setOpen(false)} />
      </aside>
    </>
  );
}

function DesktopSidebar() {
  const collapsed = useSettingsStore((s) => s.sidebarCollapsed);
  const width = collapsed ? "w-16" : "w-60";

  return (
    <aside
      className={cn(
        "hidden shrink-0 flex-col border-r border-borderSubtle bg-bgSecondary transition-[width] duration-150 md:flex",
        width,
      )}
    >
      <SidebarContent collapsed={collapsed} />
    </aside>
  );
}

export function Sidebar() {
  return (
    <>
      <DesktopSidebar />
      <MobileSidebar />
    </>
  );
}
